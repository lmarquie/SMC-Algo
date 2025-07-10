#!/bin/bash

# Setup script for Hyperliquid Order Monitor
# This script helps set up the Elixir order monitor for real-time order tracking

set -e

echo "🚀 Setting up Hyperliquid Order Monitor..."

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if Elixir is installed
check_elixir() {
    print_status "Checking Elixir installation..."
    
    if command -v elixir &> /dev/null; then
        ELIXIR_VERSION=$(elixir --version | head -n 1)
        print_success "Elixir found: $ELIXIR_VERSION"
        return 0
    else
        print_error "Elixir is not installed"
        return 1
    fi
}

# Install Elixir if not present
install_elixir() {
    print_status "Installing Elixir..."
    
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS
        if command -v brew &> /dev/null; then
            print_status "Installing Elixir via Homebrew..."
            brew install elixir
        else
            print_error "Homebrew not found. Please install Homebrew first:"
            echo "  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
            exit 1
        fi
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        # Linux
        print_status "Installing Elixir via package manager..."
        wget https://packages.erlang-solutions.com/erlang-solutions_2.0_all.deb
        sudo dpkg -i erlang-solutions_2.0_all.deb
        sudo apt-get update
        sudo apt-get install -y esl-erlang elixir
        rm erlang-solutions_2.0_all.deb
    else
        print_error "Unsupported operating system: $OSTYPE"
        print_status "Please install Elixir manually from: https://elixir-lang.org/install.html"
        exit 1
    fi
}

# Create project structure
create_project_structure() {
    print_status "Creating project structure..."
    
    # Create directories
    mkdir -p lib/hyperliquid_order_monitor
    
    print_success "Project structure created"
}

# Check if required files exist
check_required_files() {
    print_status "Checking required files..."
    
    required_files=(
        "mix.exs"
        "lib/hyperliquid_order_monitor/application.ex"
        "lib/hyperliquid_order_monitor/order_monitor_supervisor.ex"
        "lib/hyperliquid_order_monitor/websocket_manager.ex"
        "lib/hyperliquid_order_monitor/order_state_manager.ex"
        "lib/hyperliquid_order_monitor/python_comm_manager.ex"
    )
    
    missing_files=()
    
    for file in "${required_files[@]}"; do
        if [[ ! -f "$file" ]]; then
            missing_files+=("$file")
        fi
    done
    
    if [[ ${#missing_files[@]} -eq 0 ]]; then
        print_success "All required files found"
        return 0
    else
        print_error "Missing required files:"
        for file in "${missing_files[@]}"; do
            echo "  - $file"
        done
        return 1
    fi
}

# Create .env file from example
create_env_file() {
    print_status "Setting up environment configuration..."
    
    if [[ -f ".env" ]]; then
        print_warning ".env file already exists"
        read -p "Do you want to overwrite it? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            print_status "Keeping existing .env file"
            return 0
        fi
    fi
    
    if [[ -f "env.example" ]]; then
        cp env.example .env
        print_success "Created .env file from env.example"
        print_warning "⚠️  IMPORTANT: Edit .env file with your actual API keys and settings"
    else
        print_error "env.example file not found"
        return 1
    fi
}

# Install dependencies
install_dependencies() {
    print_status "Installing Elixir dependencies..."
    
    if mix deps.get; then
        print_success "Dependencies installed successfully"
    else
        print_error "Failed to install dependencies"
        exit 1
    fi
}

# Compile the project
compile_project() {
    print_status "Compiling the project..."
    
    if mix compile; then
        print_success "Project compiled successfully"
    else
        print_error "Failed to compile project"
        exit 1
    fi
}

# Check environment variables
check_environment() {
    print_status "Checking environment variables..."
    
    # Load .env file if it exists
    if [[ -f ".env" ]]; then
        print_status "Loading environment variables from .env file..."
        export $(grep -v '^#' .env | xargs)
    fi
    
    missing_vars=()
    
    if [[ -z "$HYPERLIQUID_API_KEY" ]]; then
        missing_vars+=("HYPERLIQUID_API_KEY")
    fi
    
    if [[ -z "$HYPERLIQUID_ACCOUNT_ADDRESS" ]]; then
        missing_vars+=("HYPERLIQUID_ACCOUNT_ADDRESS")
    fi
    
    if [[ ${#missing_vars[@]} -eq 0 ]]; then
        print_success "All required environment variables are set"
        return 0
    else
        print_warning "Missing environment variables:"
        for var in "${missing_vars[@]}"; do
            echo "  - $var"
        done
        print_status "Please edit the .env file and set these variables:"
        echo "  HYPERLIQUID_API_KEY=your_api_key_here"
        echo "  HYPERLIQUID_ACCOUNT_ADDRESS=your_account_address_here"
        return 1
    fi
}

# Test the setup
test_setup() {
    print_status "Testing the setup..."
    
    # Load .env file if it exists
    if [[ -f ".env" ]]; then
        export $(grep -v '^#' .env | xargs)
    fi
    
    # Test if we can start the application
    if timeout 10s mix run --no-halt &> /dev/null; then
        print_success "Setup test passed - application can start"
    else
        print_warning "Setup test failed - application may have issues starting"
        print_status "This is normal if environment variables are not set"
    fi
}

# Create a simple test script
create_test_script() {
    print_status "Creating test script..."
    
    cat > test_monitor.sh << 'EOF'
#!/bin/bash

# Test script for the Elixir order monitor

echo "🧪 Testing Elixir Order Monitor..."

# Load .env file if it exists
if [[ -f ".env" ]]; then
    echo "📄 Loading environment variables from .env file..."
    export $(grep -v '^#' .env | xargs)
fi

# Check if environment variables are set
if [[ -z "$HYPERLIQUID_API_KEY" || -z "$HYPERLIQUID_ACCOUNT_ADDRESS" ]]; then
    echo "❌ Environment variables not set"
    echo "Please edit the .env file and set:"
    echo "  HYPERLIQUID_API_KEY=your_api_key_here"
    echo "  HYPERLIQUID_ACCOUNT_ADDRESS=your_account_address_here"
    exit 1
fi

echo "✅ Environment variables set"

# Start the monitor in background
echo "🚀 Starting order monitor..."
mix run --no-halt &
MONITOR_PID=$!

# Wait a moment for it to start
sleep 5

# Check if it's still running
if kill -0 $MONITOR_PID 2>/dev/null; then
    echo "✅ Monitor is running (PID: $MONITOR_PID)"
    echo "📋 You can now run your Python trading bot"
    echo "🛑 To stop the monitor: kill $MONITOR_PID"
else
    echo "❌ Monitor failed to start"
    exit 1
fi
EOF

    chmod +x test_monitor.sh
    print_success "Test script created: test_monitor.sh"
}

# Create Python integration example
create_python_example() {
    print_status "Creating Python integration example..."
    
    cat > example_integration.py << 'EOF'
#!/usr/bin/env python3

"""
Example of how to integrate the Elixir order monitor with your Python trading bot
"""

import asyncio
import logging
from elixir_order_monitor import integrate_with_trading_bot
from env_loader import load_env

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    # Load environment variables from .env file
    load_env()
    
    # Initialize your trading bot (replace with your actual bot class)
    # from live_trading_avax import AVAXLiveTradingBot
    # bot = AVAXLiveTradingBot()
    
    # For this example, we'll create a mock bot
    class MockTradingBot:
        def __init__(self):
            self.logger = logging.getLogger(__name__)
            self.pending_order = None
            self.current_position = None
        
        def start_stop_monitoring(self):
            self.logger.info("🛑 Stop monitoring started")
        
        def monitor_pending_order(self):
            self.logger.info("📋 Monitoring pending order (will be overridden)")
    
    bot = MockTradingBot()
    
    # Integrate the Elixir order monitor
    logger.info("🔧 Integrating Elixir order monitor...")
    order_monitor = integrate_with_trading_bot(bot)
    
    if order_monitor:
        logger.info("✅ Elixir order monitor integrated successfully")
        
        # Start the monitoring loop
        logger.info("🔍 Starting monitoring loop...")
        await order_monitor.run_monitor_loop()
    else:
        logger.error("❌ Failed to integrate Elixir order monitor")

if __name__ == "__main__":
    asyncio.run(main())
EOF

    chmod +x example_integration.py
    print_success "Python integration example created: example_integration.py"
}

# Main setup process
main() {
    echo "🔧 Hyperliquid Order Monitor Setup"
    echo "=================================="
    echo
    
    # Check/install Elixir
    if ! check_elixir; then
        print_status "Elixir not found, installing..."
        install_elixir
        check_elixir
    fi
    
    # Create project structure
    create_project_structure
    
    # Check required files
    if ! check_required_files; then
        print_error "Please ensure all required files are present in the current directory"
        exit 1
    fi
    
    # Create .env file
    create_env_file
    
    # Install dependencies
    install_dependencies
    
    # Compile project
    compile_project
    
    # Check environment variables
    check_environment
    
    # Test setup
    test_setup
    
    # Create test script
    create_test_script
    
    # Create Python integration example
    create_python_example
    
    echo
    echo "🎉 Setup completed successfully!"
    echo
    echo "Next steps:"
    echo "1. Edit the .env file with your actual API keys:"
    echo "   nano .env"
    echo
    echo "2. Test the monitor:"
    echo "   ./test_monitor.sh"
    echo
    echo "3. Run with your Python bot:"
    echo "   # Terminal 1: Start Elixir monitor"
    echo "   mix run --no-halt"
    echo
    echo "   # Terminal 2: Run Python bot"
    echo "   python live_trading_avax.py"
    echo
    echo "4. Or use the integration example:"
    echo "   python example_integration.py"
    echo
    echo "📖 For more information, see: ELIXIR_ORDER_MONITOR_README.md"
}

# Run main function
main "$@" 