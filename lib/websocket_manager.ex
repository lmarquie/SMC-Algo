defmodule HyperliquidOrderMonitor.WebSocketManager do
  use GenServer
  require Logger

  @http_url "https://api.hyperliquid.xyz/info"
  @comm_file "order_updates.json"

  def start_link(config) do
    Logger.info("🔌 Starting HTTP Polling Manager...")
    GenServer.start_link(__MODULE__, config, name: __MODULE__)
  end

  def init(config) do
    Logger.info("✅ HTTP Polling Manager initialized")
    schedule_poll(config.check_interval)
    {:ok, %{
      api_key: config.api_key,
      account_address: config.account_address,
      check_interval: config.check_interval,
      last_check: nil
    }}
  end

  def handle_info(:poll_user_state, state) do
    user = state.account_address
    Logger.debug("🔍 Polling user state for: #{user}")

    open_orders = post_info(%{"type" => "openOrders", "user" => user})
    fills = post_info(%{"type" => "userFills", "user" => user})
    positions = post_info(%{"type" => "clearinghouseState", "user" => user})

    combined = %{
      "timestamp" => DateTime.utc_now() |> DateTime.to_iso8601(),
      "open_orders" => open_orders,
      "fills" => fills,
      "positions" => positions
    }

    File.write!(@comm_file, Jason.encode!(combined, pretty: true))
    Logger.info("📤 Wrote combined user state to #{@comm_file}")

    schedule_poll(state.check_interval)
    {:noreply, %{state | last_check: DateTime.utc_now()}}
  end

  defp schedule_poll(interval) do
    Process.send_after(self(), :poll_user_state, interval)
  end

  defp post_info(body) do
    headers = [{"Content-Type", "application/json"}]
    case HTTPoison.post(@http_url, Jason.encode!(body), headers) do
      {:ok, %HTTPoison.Response{status_code: 200, body: resp}} ->
        case Jason.decode(resp) do
          {:ok, data} -> data
          _ -> nil
        end
      _ -> nil
    end
  end

  def get_status do
    GenServer.call(__MODULE__, :get_status)
  end

  def handle_call(:get_status, _from, state) do
    status = %{
      connected: true,
      last_check: state.last_check,
      check_interval: state.check_interval,
      account_address: state.account_address
    }
    {:reply, status, state}
  end
end 