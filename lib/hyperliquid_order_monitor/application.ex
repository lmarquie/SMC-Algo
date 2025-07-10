defmodule HyperliquidOrderMonitor.Application do
  use Application
  require Logger

  def start(_type, _args) do
    Logger.info("🚀 Starting Hyperliquid Order Monitor Application...")
    
    # Load environment variables from .env file
    load_env_file()
    
    # Get configuration from environment
    api_key = System.get_env("HYPERLIQUID_API_KEY")
    account_address = System.get_env("HYPERLIQUID_ACCOUNT_ADDRESS")
    
    if is_nil(api_key) or is_nil(account_address) do
      Logger.error("❌ Error: HYPERLIQUID_API_KEY and HYPERLIQUID_ACCOUNT_ADDRESS must be set")
      Logger.error("   Please create a .env file with these variables or set them in your environment")
      System.halt(1)
    end
    
    # Define child processes
    children = [
      # Start the order monitor supervisor
      {HyperliquidOrderMonitor.OrderMonitorSupervisor, 
       %{
         api_key: api_key,
         account_address: account_address,
         python_comm_file: System.get_env("ELIXIR_COMM_FILE") || "order_updates.json",
         ws_url: System.get_env("ELIXIR_WS_URL") || "wss://api.hyperliquid.xyz/ws",
         http_url: System.get_env("ELIXIR_HTTP_URL") || "https://api.hyperliquid.xyz",
         check_interval: parse_int(System.get_env("ELIXIR_CHECK_INTERVAL"), 1000),
         reconnect_delay: parse_int(System.get_env("ELIXIR_RECONNECT_DELAY"), 5000),
         max_reconnect_attempts: parse_int(System.get_env("ELIXIR_MAX_RECONNECT_ATTEMPTS"), 10),
         heartbeat_interval: parse_int(System.get_env("ELIXIR_HEARTBEAT_INTERVAL"), 30000)
       }}
    ]
    
    # Start the supervision tree
    opts = [strategy: :one_for_one, name: HyperliquidOrderMonitor.Supervisor]
    
    case Supervisor.start_link(children, opts) do
      {:ok, pid} ->
        Logger.info("✅ Hyperliquid Order Monitor Application started successfully")
        Logger.info("📋 Monitoring orders for account: #{account_address}")
        Logger.info("📁 Updates will be written to: #{System.get_env("ELIXIR_COMM_FILE") || "order_updates.json"}")
        Logger.info("🔌 WebSocket URL: #{System.get_env("ELIXIR_WS_URL") || "wss://api.hyperliquid.xyz/ws"}")
        {:ok, pid}
      
      {:error, reason} ->
        Logger.error("❌ Failed to start Hyperliquid Order Monitor Application: #{reason}")
        {:error, reason}
    end
  end

  # Load environment variables from .env file
  defp load_env_file do
    env_file = ".env"
    
    if File.exists?(env_file) do
      Logger.info("📄 Loading environment variables from .env file...")
      
      # Try to use ConfigParserEx if available, otherwise use simple parsing
      case try_load_env_with_configparser(env_file) do
        {:ok, config} ->
          # Set environment variables from .env file
          Enum.each(config, fn {key, value} ->
            System.put_env(key, value)
            Logger.debug("  Set #{key}=#{String.slice(value, 0, 10)}...")
          end)
          
          Logger.info("✅ Loaded #{map_size(config)} environment variables from .env file")
        
        {:error, reason} ->
          Logger.warning("⚠️ Failed to parse .env file: #{reason}")
          Logger.info("   Continuing with system environment variables only")
      end
    else
      Logger.info("📄 No .env file found, using system environment variables only")
      Logger.info("   Create a .env file based on env.example for easier configuration")
    end
  end

  # Try to load .env file with ConfigParserEx, fallback to simple parsing
  defp try_load_env_with_configparser(env_file) do
    # Check if ConfigParserEx is available
    if Code.ensure_loaded(ConfigParserEx) == {:module, ConfigParserEx} do
      ConfigParserEx.parse_file(env_file)
    else
      # Fallback to simple .env file parsing
      parse_env_file_simple(env_file)
    end
  end

  # Simple .env file parser as fallback
  defp parse_env_file_simple(env_file) do
    case File.read(env_file) do
      {:ok, content} ->
        lines = String.split(content, "\n", trim: true)
        
        config = Enum.reduce(lines, %{}, fn line, acc ->
          line = String.trim(line)
          
          # Skip comments and empty lines
          if String.starts_with?(line, "#") or line == "" do
            acc
          else
            # Parse KEY=value format
            case String.split(line, "=", parts: 2) do
              [key, value] ->
                key = String.trim(key)
                value = String.trim(value)
                
                # Remove quotes if present
                value = String.trim(value, "\"'")
                
                Map.put(acc, key, value)
              
              _ ->
                acc
            end
          end
        end)
        
        {:ok, config}
      
      {:error, reason} ->
        {:error, "Failed to read .env file: #{reason}"}
    end
  end

  # Parse integer with default value
  defp parse_int(value, default) when is_binary(value) do
    case Integer.parse(value) do
      {int, _} -> int
      :error -> default
    end
  end
  
  defp parse_int(_, default), do: default
end 