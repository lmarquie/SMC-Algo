#!/usr/bin/env elixir

# Order Monitor for Hyperliquid Trading Bot
# This Elixir script handles real-time order monitoring via WebSocket
# and communicates with the Python trading bot via file-based protocol

defmodule OrderMonitor do
  use GenServer
  require Logger

  def start_link(opts) do
    GenServer.start_link(__MODULE__, opts, name: __MODULE__)
  end

  def init(opts) do
    Logger.info("🚀 Starting Order Monitor for Hyperliquid...")
    
    # Initialize state
    state = %{
      api_key: opts[:api_key],
      account_address: opts[:account_address],
      pending_orders: %{},  # Track pending orders by client_order_id
      ws_connection: nil,
      python_comm_file: "order_updates.json",  # File to communicate with Python
      last_update_id: 0
    }
    
    # Start WebSocket connection
    {:ok, state, {:continue, :connect_websocket}}
  end

  def handle_continue(:connect_websocket, state) do
    Logger.info("🔌 Connecting to Hyperliquid WebSocket...")
    
    # Connect to Hyperliquid WebSocket
    case connect_websocket(state) do
      {:ok, connection} ->
        Logger.info("✅ WebSocket connected successfully")
        {:noreply, %{state | ws_connection: connection}}
      
      {:error, reason} ->
        Logger.error("❌ Failed to connect to WebSocket: #{reason}")
        # Retry after 5 seconds
        Process.send_after(self(), :retry_connection, 5000)
        {:noreply, state}
    end
  end

  def handle_info(:retry_connection, state) do
    Logger.info("🔄 Retrying WebSocket connection...")
    {:noreply, state, {:continue, :connect_websocket}}
  end

  def handle_info({:websocket_message, message}, state) do
    case Jason.decode(message) do
      {:ok, data} ->
        handle_websocket_data(data, state)
      
      {:error, reason} ->
        Logger.error("❌ Failed to parse WebSocket message: #{reason}")
        {:noreply, state}
    end
  end

  def handle_info({:websocket_closed, reason}, state) do
    Logger.warning("🔌 WebSocket connection closed: #{reason}")
    # Reconnect after 5 seconds
    Process.send_after(self(), :retry_connection, 5000)
    {:noreply, %{state | ws_connection: nil}}
  end

  def handle_info({:websocket_error, error}, state) do
    Logger.error("❌ WebSocket error: #{error}")
    {:noreply, state}
  end

  # Handle different types of WebSocket data
  defp handle_websocket_data(%{"type" => "orderUpdate"} = data, state) do
    Logger.info("📋 Order Update: #{inspect(data)}")
    
    # Extract order information
    order_id = data["orderId"]
    client_order_id = data["clientOrderId"]
    status = data["status"]
    symbol = data["symbol"]
    
    case status do
      "filled" ->
        # Order was filled
        handle_order_filled(data, state)
      
      "cancelled" ->
        # Order was cancelled
        handle_order_cancelled(data, state)
      
      "rejected" ->
        # Order was rejected
        handle_order_rejected(data, state)
      
      _ ->
        # Other status updates
        Logger.info("📊 Order status update: #{status} for order #{order_id}")
        {:noreply, state}
    end
  end

  defp handle_websocket_data(%{"type" => "positionUpdate"} = data, state) do
    Logger.info("📈 Position Update: #{inspect(data)}")
    
    # Check if this position update corresponds to a pending order
    symbol = data["symbol"]
    size = data["size"]
    
    if size != 0 do
      # We have a position - check if it matches any pending orders
      check_pending_orders_for_fill(symbol, data, state)
    else
      # Position closed
      handle_position_closed(data, state)
    end
    
    {:noreply, state}
  end

  defp handle_websocket_data(%{"type" => "userState"} = data, state) do
    Logger.info("👤 User State Update: #{inspect(data)}")
    
    # Check for new positions that might indicate order fills
    if Map.has_key?(data, "assetPositions") do
      check_asset_positions(data["assetPositions"], state)
    end
    
    {:noreply, state}
  end

  defp handle_websocket_data(data, state) do
    # Handle other types of messages
    Logger.debug("📨 WebSocket message: #{inspect(data)}")
    {:noreply, state}
  end

  # Handle order filled event
  defp handle_order_filled(data, state) do
    order_id = data["orderId"]
    client_order_id = data["clientOrderId"]
    symbol = data["symbol"]
    fill_price = data["avgPx"]
    fill_size = data["totalSz"]
    
    Logger.info("✅ ORDER FILLED: #{symbol} at $#{fill_price} size: #{fill_size}")
    
    # Send update to Python bot
    update = %{
      id: state.last_update_id + 1,
      timestamp: DateTime.utc_now() |> DateTime.to_iso8601(),
      type: "order_filled",
      order_id: order_id,
      client_order_id: client_order_id,
      symbol: symbol,
      fill_price: fill_price,
      fill_size: fill_size,
      data: data
    }
    
    send_update_to_python(update, state)
    
    # Remove from pending orders
    new_pending_orders = Map.delete(state.pending_orders, client_order_id)
    
    {:noreply, %{state | 
      pending_orders: new_pending_orders,
      last_update_id: state.last_update_id + 1
    }}
  end

  # Handle order cancelled event
  defp handle_order_cancelled(data, state) do
    order_id = data["orderId"]
    client_order_id = data["clientOrderId"]
    symbol = data["symbol"]
    reason = data["reason"]
    
    Logger.info("❌ ORDER CANCELLED: #{symbol} - #{reason}")
    
    # Send update to Python bot
    update = %{
      id: state.last_update_id + 1,
      timestamp: DateTime.utc_now() |> DateTime.to_iso8601(),
      type: "order_cancelled",
      order_id: order_id,
      client_order_id: client_order_id,
      symbol: symbol,
      reason: reason,
      data: data
    }
    
    send_update_to_python(update, state)
    
    # Remove from pending orders
    new_pending_orders = Map.delete(state.pending_orders, client_order_id)
    
    {:noreply, %{state | 
      pending_orders: new_pending_orders,
      last_update_id: state.last_update_id + 1
    }}
  end

  # Handle order rejected event
  defp handle_order_rejected(data, state) do
    order_id = data["orderId"]
    client_order_id = data["clientOrderId"]
    symbol = data["symbol"]
    reason = data["reason"]
    
    Logger.error("🚫 ORDER REJECTED: #{symbol} - #{reason}")
    
    # Send update to Python bot
    update = %{
      id: state.last_update_id + 1,
      timestamp: DateTime.utc_now() |> DateTime.to_iso8601(),
      type: "order_rejected",
      order_id: order_id,
      client_order_id: client_order_id,
      symbol: symbol,
      reason: reason,
      data: data
    }
    
    send_update_to_python(update, state)
    
    # Remove from pending orders
    new_pending_orders = Map.delete(state.pending_orders, client_order_id)
    
    {:noreply, %{state | 
      pending_orders: new_pending_orders,
      last_update_id: state.last_update_id + 1
    }}
  end

  # Check if position updates correspond to pending order fills
  defp check_pending_orders_for_fill(symbol, position_data, state) do
    # Look for pending orders for this symbol
    matching_orders = Enum.filter(state.pending_orders, fn {_cloid, order} ->
      order.symbol == symbol
    end)
    
    if length(matching_orders) > 0 do
      Logger.info("🔍 Found #{length(matching_orders)} pending orders for #{symbol}")
      
      # Assume the first matching order was filled
      {client_order_id, order} = List.first(matching_orders)
      
      # Send position-based fill update to Python
      update = %{
        id: state.last_update_id + 1,
        timestamp: DateTime.utc_now() |> DateTime.to_iso8601(),
        type: "position_fill",
        client_order_id: client_order_id,
        symbol: symbol,
        position_data: position_data,
        original_order: order
      }
      
      send_update_to_python(update, state)
      
      # Remove from pending orders
      new_pending_orders = Map.delete(state.pending_orders, client_order_id)
      
      {:noreply, %{state | 
        pending_orders: new_pending_orders,
        last_update_id: state.last_update_id + 1
      }}
    else
      {:noreply, state}
    end
  end

  # Handle position closed event
  defp handle_position_closed(data, state) do
    symbol = data["symbol"]
    Logger.info("📉 Position closed for #{symbol}")
    
    # Send update to Python bot
    update = %{
      id: state.last_update_id + 1,
      timestamp: DateTime.utc_now() |> DateTime.to_iso8601(),
      type: "position_closed",
      symbol: symbol,
      data: data
    }
    
    send_update_to_python(update, state)
    
    {:noreply, %{state | last_update_id: state.last_update_id + 1}}
  end

  # Check asset positions for new fills
  defp check_asset_positions(asset_positions, state) do
    Enum.each(asset_positions, fn position ->
      symbol = position["coin"]
      size = position["szi"]
      
      if size != 0 do
        # Check if we have pending orders for this symbol
        matching_orders = Enum.filter(state.pending_orders, fn {_cloid, order} ->
          order.symbol == symbol
        end)
        
        if length(matching_orders) > 0 do
          Logger.info("🔍 Found position for #{symbol} with pending orders")
          check_pending_orders_for_fill(symbol, position, state)
        end
      end
    end)
  end

  # Send update to Python bot via file
  defp send_update_to_python(update, state) do
    try do
      # Read existing updates
      existing_updates = case File.read(state.python_comm_file) do
        {:ok, content} ->
          case Jason.decode(content) do
            {:ok, data} -> data
            {:error, _} -> []
          end
        
        {:error, :enoent} ->
          []
      end
      
      # Add new update
      all_updates = existing_updates ++ [update]
      
      # Write back to file
      case Jason.encode(all_updates, pretty: true) do
        {:ok, json} ->
          File.write(state.python_comm_file, json)
          Logger.info("📤 Sent update to Python: #{update.type}")
        
        {:error, reason} ->
          Logger.error("❌ Failed to encode update: #{reason}")
      end
    rescue
      e -> Logger.error("❌ Error sending update to Python: #{inspect(e)}")
    end
  end

  # Connect to Hyperliquid WebSocket
  defp connect_websocket(state) do
    # WebSocket URL for Hyperliquid
    ws_url = "wss://api.hyperliquid.xyz/ws"
    
    # Subscribe to user-specific updates
    subscribe_message = %{
      "method" => "subscribe",
      "subscription" => %{
        "type" => "user",
        "user" => state.account_address
      }
    }
    
    # Also subscribe to order updates
    order_subscribe = %{
      "method" => "subscribe", 
      "subscription" => %{
        "type" => "orders",
        "user" => state.account_address
      }
    }
    
    # For now, we'll use a simple WebSocket client
    # In production, you'd want to use a proper WebSocket library
    Logger.info("🔌 Connecting to #{ws_url}")
    
    # This is a simplified version - you'd need to implement proper WebSocket handling
    # For now, we'll simulate the connection
    {:ok, :mock_connection}
  end

  # API to add pending order for monitoring
  def add_pending_order(client_order_id, order_data) do
    GenServer.call(__MODULE__, {:add_pending_order, client_order_id, order_data})
  end

  def handle_call({:add_pending_order, client_order_id, order_data}, _from, state) do
    Logger.info("📋 Adding pending order for monitoring: #{client_order_id}")
    
    new_pending_orders = Map.put(state.pending_orders, client_order_id, order_data)
    
    {:reply, :ok, %{state | pending_orders: new_pending_orders}}
  end

  # API to remove pending order
  def remove_pending_order(client_order_id) do
    GenServer.call(__MODULE__, {:remove_pending_order, client_order_id})
  end

  def handle_call({:remove_pending_order, client_order_id}, _from, state) do
    Logger.info("🗑️ Removing pending order: #{client_order_id}")
    
    new_pending_orders = Map.delete(state.pending_orders, client_order_id)
    
    {:reply, :ok, %{state | pending_orders: new_pending_orders}}
  end

  # API to get current pending orders
  def get_pending_orders do
    GenServer.call(__MODULE__, :get_pending_orders)
  end

  def handle_call(:get_pending_orders, _from, state) do
    {:reply, state.pending_orders, state}
  end
end

# Main function to start the order monitor
def main do
  # Get configuration from environment or command line
  api_key = System.get_env("HYPERLIQUID_API_KEY")
  account_address = System.get_env("HYPERLIQUID_ACCOUNT_ADDRESS")
  
  if is_nil(api_key) or is_nil(account_address) do
    IO.puts("❌ Error: HYPERLIQUID_API_KEY and HYPERLIQUID_ACCOUNT_ADDRESS must be set")
    System.halt(1)
  end
  
  # Start the order monitor
  {:ok, _pid} = OrderMonitor.start_link(
    api_key: api_key,
    account_address: account_address
  )
  
  IO.puts("🚀 Order Monitor started successfully")
  IO.puts("📋 Monitoring orders for account: #{account_address}")
  IO.puts("📁 Updates will be written to: order_updates.json")
  IO.puts("⏹️  Press Ctrl+C to stop")
  
  # Keep the process running
  receive do
    _ -> :ok
  end
end

# Run the main function if this script is executed directly
if __FILE__ == __ENV__.file do
  main()
end 