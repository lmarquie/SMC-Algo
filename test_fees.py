import asyncio
import logging
from datetime import datetime
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants
from eth_account import Account
from config import *

class FeeTestBot:
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
            # Get current price from info endpoint
            meta = self.info.meta()
            for asset in meta['universe']:
                if asset['name'] == 'AVAX':
                    return float(asset['markPrice'])
        except Exception as e:
            self.logger.error(f"Error getting current price: {e}")
            return None
    
    async def test_buy_fee(self):
        """Test buying with ALO limit order"""
        self.logger.info("🧪 TESTING BUY FEE WITH ALO LIMIT ORDER")
        
        current_price = self.get_current_price()
        if not current_price:
            self.logger.error("❌ Could not get current price")
            return
        
        # Place a small buy order slightly below market (should be post-only)
        test_size = 0.01  # Very small size
        limit_price = current_price * 0.99  # 1% below market
        
        self.logger.info(f"📈 Placing BUY order:")
        self.logger.info(f"  Size: {test_size} AVAX")
        self.logger.info(f"  Limit Price: ${limit_price:.4f}")
        self.logger.info(f"  Current Market: ${current_price:.4f}")
        
        try:
            order_result = self.exchange.order(
                name="AVAX",
                is_buy=True,
                sz=test_size,
                limit_px=limit_price,
                order_type={"limit": {"tif": "Alo"}},
                reduce_only=False
            )
            
            self.logger.info(f"📋 BUY ORDER RESULT: {order_result}")
            
            if order_result and 'status' in order_result and order_result['status'] == 'ok':
                filled_data = order_result.get('response', {}).get('data', {}).get('statuses', [{}])[0].get('filled', {})
                
                if filled_data:
                    self.logger.info(f"✅ BUY ORDER FILLED!")
                    self.logger.info(f"  Fill Price: ${float(filled_data.get('avgPx', 0)):.4f}")
                    self.logger.info(f"  Fill Size: {float(filled_data.get('totalSz', 0))}")
                    self.logger.info(f"  Fee: Check your Hyperliquid dashboard for fee details")
                    
                    # Now test selling it back
                    await self.test_sell_fee()
                else:
                    self.logger.info(f"⏳ BUY ORDER PLACED BUT NOT FILLED (post-only)")
                    self.logger.info(f"  Order ID: {order_result.get('response', {}).get('data', {}).get('statuses', [{}])[0].get('oid')}")
                    
                    # Cancel the order after 30 seconds
                    await asyncio.sleep(30)
                    order_id = order_result.get('response', {}).get('data', {}).get('statuses', [{}])[0].get('oid')
                    if order_id:
                        cancel_result = self.exchange.cancel_order("AVAX", order_id)
                        self.logger.info(f"❌ Cancelled unfilled order: {cancel_result}")
            else:
                self.logger.error(f"❌ BUY ORDER FAILED: {order_result}")
                
        except Exception as e:
            self.logger.error(f"❌ Error placing buy order: {e}")
    
    async def test_sell_fee(self):
        """Test selling with ALO limit order"""
        self.logger.info("🧪 TESTING SELL FEE WITH ALO LIMIT ORDER")
        
        current_price = self.get_current_price()
        if not current_price:
            self.logger.error("❌ Could not get current price")
            return
        
        # Place a small sell order slightly above market (should be post-only)
        test_size = 0.01  # Very small size
        limit_price = current_price * 1.01  # 1% above market
        
        self.logger.info(f"📉 Placing SELL order:")
        self.logger.info(f"  Size: {test_size} AVAX")
        self.logger.info(f"  Limit Price: ${limit_price:.4f}")
        self.logger.info(f"  Current Market: ${current_price:.4f}")
        
        try:
            order_result = self.exchange.order(
                name="AVAX",
                is_buy=False,
                sz=test_size,
                limit_px=limit_price,
                order_type={"limit": {"tif": "Alo"}},
                reduce_only=True
            )
            
            self.logger.info(f"📋 SELL ORDER RESULT: {order_result}")
            
            if order_result and 'status' in order_result and order_result['status'] == 'ok':
                filled_data = order_result.get('response', {}).get('data', {}).get('statuses', [{}])[0].get('filled', {})
                
                if filled_data:
                    self.logger.info(f"✅ SELL ORDER FILLED!")
                    self.logger.info(f"  Fill Price: ${float(filled_data.get('avgPx', 0)):.4f}")
                    self.logger.info(f"  Fill Size: {float(filled_data.get('totalSz', 0))}")
                    self.logger.info(f"  Fee: Check your Hyperliquid dashboard for fee details")
                else:
                    self.logger.info(f"⏳ SELL ORDER PLACED BUT NOT FILLED (post-only)")
                    self.logger.info(f"  Order ID: {order_result.get('response', {}).get('data', {}).get('statuses', [{}])[0].get('oid')}")
                    
                    # Cancel the order after 30 seconds
                    await asyncio.sleep(30)
                    order_id = order_result.get('response', {}).get('data', {}).get('statuses', [{}])[0].get('oid')
                    if order_id:
                        cancel_result = self.exchange.cancel_order("AVAX", order_id)
                        self.logger.info(f"❌ Cancelled unfilled order: {cancel_result}")
            else:
                self.logger.error(f"❌ SELL ORDER FAILED: {order_result}")
                
        except Exception as e:
            self.logger.error(f"❌ Error placing sell order: {e}")
    
    async def run_fee_test(self):
        """Run the complete fee test"""
        self.logger.info("🚀 STARTING FEE TEST FOR AVAX ALO ORDERS")
        self.logger.info("This will place small test orders to check fees")
        self.logger.info("Check your Hyperliquid dashboard for fee details")
        
        # Test buying first
        await self.test_buy_fee()
        
        self.logger.info("🧪 FEE TEST COMPLETED")
        self.logger.info("Check your Hyperliquid dashboard for detailed fee breakdown")

async def main():
    bot = FeeTestBot()
    await bot.run_fee_test()

if __name__ == "__main__":
    print("🧪 AVAX Fee Test Bot")
    print("This will place small test orders to check ALO fees")
    print("Make sure you have sufficient balance for small test orders")
    print()
    
    asyncio.run(main()) 