import asyncio
import logging
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants
from eth_account import Account
from config import *

class OrderPlacementTest:
    def __init__(self):
        # Initialize the Hyperliquid SDK clients
        self.wallet = Account.from_key(HYPERLIQUID_API_KEY)
        self.info = Info(constants.MAINNET_API_URL, skip_ws=True)
        self.exchange = Exchange(
            wallet=self.wallet,
            base_url=constants.MAINNET_API_URL,
            account_address=HYPERLIQUID_ACCOUNT_ADDRESS
        )
        
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)
    
    def get_current_price(self):
        """Get current AVAX price"""
        try:
            meta = self.info.meta()
            for asset in meta['universe']:
                if asset['name'] == 'AVAX':
                    return float(asset['markPrice'])
        except Exception as e:
            self.logger.error(f"Error getting current price: {e}")
            return None
    
    async def test_market_order(self):
        """Test a simple market order to verify basic functionality"""
        self.logger.info("🧪 TESTING MARKET ORDER PLACEMENT")
        
        current_price = self.get_current_price()
        if not current_price:
            self.logger.error("❌ Could not get current price")
            return
        
        test_size = 0.01  # Very small size
        
        self.logger.info(f"📈 Placing MARKET BUY order:")
        self.logger.info(f"  Size: {test_size} AVAX")
        self.logger.info(f"  Current Market: ${current_price:.4f}")
        
        try:
            order_result = self.exchange.order(
                name="AVAX",
                is_buy=True,
                sz=test_size,
                order_type={"market": {}},
                reduce_only=False
            )
            
            self.logger.info(f"📋 MARKET ORDER RESULT: {order_result}")
            
            if order_result and 'status' in order_result and order_result['status'] == 'ok':
                filled_data = order_result.get('response', {}).get('data', {}).get('statuses', [{}])[0].get('filled', {})
                
                if filled_data:
                    self.logger.info(f"✅ MARKET ORDER FILLED!")
                    self.logger.info(f"  Fill Price: ${float(filled_data.get('avgPx', 0)):.4f}")
                    self.logger.info(f"  Fill Size: {float(filled_data.get('totalSz', 0))}")
                    
                    # Now test a limit order
                    await self.test_limit_order()
                else:
                    self.logger.error(f"❌ MARKET ORDER NOT FILLED: {order_result}")
            else:
                self.logger.error(f"❌ MARKET ORDER FAILED: {order_result}")
                
        except Exception as e:
            self.logger.error(f"❌ Error placing market order: {e}")
    
    async def test_limit_order(self):
        """Test a limit order to see if it appears in open orders"""
        self.logger.info("🧪 TESTING LIMIT ORDER PLACEMENT")
        
        current_price = self.get_current_price()
        if not current_price:
            self.logger.error("❌ Could not get current price")
            return
        
        test_size = 0.01  # Very small size
        limit_price = current_price * 0.95  # 5% below market (should be post-only)
        
        self.logger.info(f"📈 Placing LIMIT SELL order:")
        self.logger.info(f"  Size: {test_size} AVAX")
        self.logger.info(f"  Limit Price: ${limit_price:.4f}")
        self.logger.info(f"  Current Market: ${current_price:.4f}")
        self.logger.info(f"  Check Hyperliquid dashboard for open orders...")
        
        try:
            order_result = self.exchange.order(
                name="AVAX",
                is_buy=False,
                sz=test_size,
                limit_px=limit_price,
                order_type={"limit": {"tif": "Alo"}},
                reduce_only=True
            )
            
            self.logger.info(f"📋 LIMIT ORDER RESULT: {order_result}")
            
            if order_result and 'status' in order_result and order_result['status'] == 'ok':
                filled_data = order_result.get('response', {}).get('data', {}).get('statuses', [{}])[0].get('filled', {})
                order_id = order_result.get('response', {}).get('data', {}).get('statuses', [{}])[0].get('oid')
                
                if filled_data:
                    self.logger.info(f"✅ LIMIT ORDER FILLED IMMEDIATELY!")
                    self.logger.info(f"  Fill Price: ${float(filled_data.get('avgPx', 0)):.4f}")
                else:
                    self.logger.info(f"⏳ LIMIT ORDER PLACED BUT NOT FILLED")
                    self.logger.info(f"  Order ID: {order_id}")
                    self.logger.info(f"  Check 'Open Orders' section in Hyperliquid dashboard")
                    self.logger.info(f"  Also check 'Post-Only Orders' or 'Pending Orders' sections")
                    
                    # Wait 30 seconds then cancel
                    self.logger.info(f"⏳ Waiting 30 seconds before cancelling...")
                    await asyncio.sleep(30)
                    
                    if order_id:
                        cancel_result = self.exchange.cancel_order("AVAX", order_id)
                        self.logger.info(f"❌ Cancelled order: {cancel_result}")
            else:
                self.logger.error(f"❌ LIMIT ORDER FAILED: {order_result}")
                
        except Exception as e:
            self.logger.error(f"❌ Error placing limit order: {e}")
    
    async def run_test(self):
        """Run the complete order placement test"""
        self.logger.info("🚀 STARTING ORDER PLACEMENT TEST")
        self.logger.info("This will test market and limit order placement")
        self.logger.info("Check your Hyperliquid dashboard during the test")
        
        await self.test_market_order()
        
        self.logger.info("🧪 ORDER PLACEMENT TEST COMPLETED")

async def main():
    bot = OrderPlacementTest()
    await bot.run_test()

if __name__ == "__main__":
    print("🧪 AVAX Order Placement Test")
    print("This will test if orders are actually being placed")
    print("Check your Hyperliquid dashboard during the test")
    print()
    
    asyncio.run(main()) 