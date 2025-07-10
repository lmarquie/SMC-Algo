defmodule HyperliquidOrderMonitor.OrderStateManager do
  use GenServer
  require Logger

  def start_link(config) do
    Logger.info("📋 Starting Order State Manager...")
    GenServer.start_link(__MODULE__, config, name: __MODULE__)
  end

  def init(config) do
    Logger.info("📋 Initializing Order State Manager...")
    
    state = %{
      pending_orders: %{},  # Track pending orders by client_order_id
      last_update_id: 0,
      python_comm_file: config.python_comm_file
    }
    
    # Ensure the communication file exists
    ensure_comm_file(state.python_comm_file)
    
    {:ok, state}
  end

  # Handle WebSocket data from the WebSocket manager
  def handle_websocket_data(data) do
    GenServer.cast(__MODULE__, {:websocket_data, data})
  end

  def handle_cast({:websocket_data, data}, state) do
    Logger.debug("📨 Processing WebSocket data: #{inspect(data)}")
    
    case data do
      %{"type" => "orderUpdate"} = order_data ->
        handle_order_update(order_data, state)
      
      %{"type" => "positionUpdate"} = position_data ->
        handle_position_update(position_data, state)
      
      %{"type" => "userState"} = user_data ->
        handle_user_state_update(user_data, state)
      
      %{"type" => "ping"} ->
        # Handle ping/pong
        Logger.debug("🏓 Received ping")
        {:noreply, state}
      
      %{"type" => "pong"} ->
        # Handle pong response
        Logger.debug("🏓 Received pong")
        {:noreply, state}
      
      _ ->
        # Handle other message types
        Logger.debug("📨 Other WebSocket message: #{inspect(data)}")
        {:noreply, state}
    end
  end

  defp handle_order_update(order_data, state) do
    Logger.info("📋 Order Update: #{inspect(order_data)}")
    
    # Extract order information
    order_id = order_data["orderId"]
    client_order_id = order_data["clientOrderId"]
    status = order_data["status"]
    symbol = order_data["symbol"]
    
    case status do
      "filled" ->
        handle_order_filled(order_data, state)
      
      "cancelled" ->
        handle_order_cancelled(order_data, state)
      
      "rejected" ->
        handle_order_rejected(order_data, state)
      
      "resting" ->
        handle_order_resting(order_data, state)
      
      _ ->
        Logger.info("📊 Order status update: #{status} for order #{order_id}")
        {:noreply, state}
    end
  end

  defp handle_position_update(position_data, state) do
    Logger.info("📈 Position Update: #{inspect(position_data)}")
    
    symbol = position_data["symbol"]
    size = position_data["size"]
    
    if size != 0 do
      # We have a position - check if it matches any pending orders
      check_pending_orders_for_fill(symbol, position_data, state)
    else
      # Position closed
      handle_position_closed(position_data, state)
    end
    
    {:noreply, state}
  end

  defp handle_user_state_update(user_data, state) do
    Logger.info("👤 User State Update: #{inspect(user_data)}")
    
    # Check for new positions that might indicate order fills
    if Map.has_key?(user_data, "assetPositions") do
      check_asset_positions(user_data["assetPositions"], state)
    end
    
    {:noreply, state}
  end

  defp handle_order_filled(order_data, state) do
    order_id = order_data["orderId"]
    client_order_id = order_data["clientOrderId"]
    symbol = order_data["symbol"]
    fill_price = order_data["avgPx"]
    fill_size = order_data["totalSz"]
    
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
      data: order_data
    }
    
    send_update_to_python(update, state)
    
    # Remove from pending orders
    new_pending_orders = Map.delete(state.pending_orders, client_order_id)
    
    {:noreply, %{state | 
      pending_orders: new_pending_orders,
      last_update_id: state.last_update_id + 1
    }}
  end

  defp handle_order_cancelled(order_data, state) do
    order_id = order_data["orderId"]
    client_order_id = order_data["clientOrderId"]
    symbol = order_data["symbol"]
    reason = order_data["reason"]
    
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
      data: order_data
    }
    
    send_update_to_python(update, state)
    
    # Remove from pending orders
    new_pending_orders = Map.delete(state.pending_orders, client_order_id)
    
    {:noreply, %{state | 
      pending_orders: new_pending_orders,
      last_update_id: state.last_update_id + 1
    }}
  end

  defp handle_order_rejected(order_data, state) do
    order_id = order_data["orderId"]
    client_order_id = order_data["clientOrderId"]
    symbol = order_data["symbol"]
    reason = order_data["reason"]
    
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
      data: order_data
    }
    
    send_update_to_python(update, state)
    
    # Remove from pending orders
    new_pending_orders = Map.delete(state.pending_orders, client_order_id)
    
    {:noreply, %{state | 
      pending_orders: new_pending_orders,
      last_update_id: state.last_update_id + 1
    }}
  end

  defp handle_order_resting(order_data, state) do
    order_id = order_data["orderId"]
    client_order_id = order_data["clientOrderId"]
    symbol = order_data["symbol"]
    
    Logger.info("⏳ ORDER RESTING: #{symbol} - #{client_order_id}")
    
    # Order is resting - keep it in pending orders
    {:noreply, state}
  end

  defp check_pending_orders_for_fill(symbol, position_data, state) do
    # Look for pending orders for this symbol
    matching_orders = Enum.filter(state.pending_orders, fn {_cloid, order} ->
      order["symbol"] == symbol
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

  defp handle_position_closed(position_data, state) do
    symbol = position_data["symbol"]
    Logger.info("📉 Position closed for #{symbol}")
    
    # Send update to Python bot
    update = %{
      id: state.last_update_id + 1,
      timestamp: DateTime.utc_now() |> DateTime.to_iso8601(),
      type: "position_closed",
      symbol: symbol,
      data: position_data
    }
    
    send_update_to_python(update, state)
    
    {:noreply, %{state | last_update_id: state.last_update_id + 1}}
  end

  defp check_asset_positions(asset_positions, state) do
    Enum.each(asset_positions, fn position ->
      symbol = position["coin"]
      size = position["szi"]
      
      if size != 0 do
        # Check if we have pending orders for this symbol
        matching_orders = Enum.filter(state.pending_orders, fn {_cloid, order} ->
          order["symbol"] == symbol
        end)
        
        if length(matching_orders) > 0 do
          Logger.info("🔍 Found position for #{symbol} with pending orders")
          check_pending_orders_for_fill(symbol, position, state)
        end
      end
    end)
  end

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

  defp ensure_comm_file(filename) do
    unless File.exists?(filename) do
      File.write(filename, Jason.encode!([]))
      Logger.info("📁 Created communication file: #{filename}")
    end
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

  # API to get status
  def get_status do
    GenServer.call(__MODULE__, :get_status)
  end

  def handle_call(:get_status, _from, state) do
    status = %{
      pending_orders_count: map_size(state.pending_orders),
      last_update_id: state.last_update_id,
      python_comm_file: state.python_comm_file
    }
    {:reply, status, state}
  end
end 