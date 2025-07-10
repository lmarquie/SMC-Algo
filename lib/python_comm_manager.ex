defmodule HyperliquidOrderMonitor.PythonCommManager do
  use GenServer
  require Logger

  def start_link(config) do
    Logger.info("🐍 Starting Python Communication Manager...")
    GenServer.start_link(__MODULE__, config, name: __MODULE__)
  end

  def init(config) do
    Logger.info("🐍 Initializing Python Communication Manager...")
    
    state = %{
      python_comm_file: config.python_comm_file,
      last_file_size: 0,
      check_interval: config.check_interval
    }
    
    # Ensure the communication file exists
    ensure_comm_file(state.python_comm_file)
    
    # Start monitoring for new pending orders from Python
    Process.send_after(self(), :check_pending_orders, state.check_interval)
    
    {:ok, state}
  end

  def handle_info(:check_pending_orders, state) do
    # Check for new pending order files from Python
    check_for_pending_orders(state)
    
    # Schedule next check
    Process.send_after(self(), :check_pending_orders, state.check_interval)
    
    {:noreply, state}
  end

  defp check_for_pending_orders(state) do
    # Look for pending order files in the current directory
    case File.ls(".") do
      {:ok, files} ->
        pending_order_files = Enum.filter(files, fn file ->
          String.starts_with?(file, "pending_order_") and String.ends_with?(file, ".json")
        end)
        
        Enum.each(pending_order_files, fn file ->
          process_pending_order_file(file, state)
        end)
      
      {:error, reason} ->
        Logger.error("❌ Error listing directory: #{reason}")
    end
  end

  defp process_pending_order_file(filename, state) do
    try do
      case File.read(filename) do
        {:ok, content} ->
          case Jason.decode(content) do
            {:ok, data} ->
              client_order_id = data["client_order_id"]
              order_data = data["order_data"]
              
              Logger.info("📋 Processing pending order file: #{filename}")
              Logger.info("  Client Order ID: #{client_order_id}")
              Logger.info("  Symbol: #{order_data["symbol"]}")
              Logger.info("  Direction: #{order_data["direction"]}")
              
              # Add the order to the state manager for monitoring
              HyperliquidOrderMonitor.OrderStateManager.add_pending_order(client_order_id, order_data)
              
              # Remove the file after processing
              File.rm(filename)
              Logger.info("🗑️ Removed processed pending order file: #{filename}")
            
            {:error, reason} ->
              Logger.error("❌ Failed to parse pending order file #{filename}: #{reason}")
              # Remove the invalid file
              File.rm(filename)
          end
        
        {:error, reason} ->
          Logger.error("❌ Failed to read pending order file #{filename}: #{reason}")
      end
    rescue
      e ->
        Logger.error("❌ Exception processing pending order file #{filename}: #{inspect(e)}")
        # Try to remove the problematic file
        File.rm(filename)
    end
  end

  defp ensure_comm_file(filename) do
    unless File.exists?(filename) do
      File.write(filename, Jason.encode!([]))
      Logger.info("📁 Created communication file: #{filename}")
    end
  end

  # API to get status
  def get_status do
    GenServer.call(__MODULE__, :get_status)
  end

  def handle_call(:get_status, _from, state) do
    # Count pending order files
    pending_files_count = case File.ls(".") do
      {:ok, files} ->
        Enum.count(files, fn file ->
          String.starts_with?(file, "pending_order_") and String.ends_with?(file, ".json")
        end)
      
      {:error, _} ->
        0
    end
    
    status = %{
      python_comm_file: state.python_comm_file,
      check_interval: state.check_interval,
      pending_files_count: pending_files_count
    }
    
    {:reply, status, state}
  end
end 