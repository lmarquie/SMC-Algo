defmodule HyperliquidOrderMonitor.MixProject do
  use Mix.Project

  def project do
    [
      app: :hyperliquid_order_monitor,
      version: "0.1.0",
      elixir: "~> 1.14",
      start_permanent: Mix.env() == :prod,
      deps: deps(),
      escript: [main_module: HyperliquidOrderMonitor, name: "order_monitor"]
    ]
  end

  def application do
    [
      extra_applications: [:logger],
      mod: {HyperliquidOrderMonitor.Application, []}
    ]
  end

  defp deps do
    [
      {:jason, "~> 1.4"},
      {:configparser_ex, "~> 4.0"},
      {:websock_adapter, "~> 0.5"},
      {:websockex, "~> 0.4"},
      {:httpoison, "~> 2.0"}
    ]
  end
end 