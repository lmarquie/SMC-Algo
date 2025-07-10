defmodule HyperliquidOrderMonitor.OrderMonitorSupervisor do
  use Supervisor
  require Logger

  def start_link(config) do
    Logger.info("🔧 Starting Order Monitor Supervisor...")
    Supervisor.start_link(__MODULE__, config, name: __MODULE__)
  end

  def init(config) do
    Logger.info("🔧 Initializing Order Monitor Supervisor with config: #{inspect(config)}")
    
    children = [
      # WebSocket connection manager
      {HyperliquidOrderMonitor.WebSocketManager, config},
      
      # Order state manager
      {HyperliquidOrderMonitor.OrderStateManager, config},
      
      # Python communication manager
      {HyperliquidOrderMonitor.PythonCommManager, config}
    ]
    
    Supervisor.init(children, strategy: :one_for_one)
  end
end 