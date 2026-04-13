"""
CryptoBot - Spot Trading Bot
Version Gate.io ETH/USDT: 15min, RSI 35, profit 0.5%, take-profit 1.5%, allocation 30%
"""

import os
import ccxt
import time
import pandas as pd
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
import threading

SYMBOL = 'ETH/USDT'
TIMEFRAME = '15m'
PAPER_MODE = False

API_KEY = os.getenv('GATEIO_API_KEY', '')
API_SECRET = os.getenv('GATEIO_API_SECRET', '')

TRADING_FEE = 0.001
MIN_USDT_RESERVE = 1
MIN_PROFIT_THRESHOLD = 0.5
TAKE_PROFIT_THRESHOLD = 1.5
RSI_BUY_THRESHOLD = 35
MAX_USDT_PERCENT = 30

class SimpleBot:
    def __init__(self):
        if PAPER_MODE:
            print("Mode : PAPER TRADING")
            self.exchange = ccxt.gateio({'enableRateLimit': True})
            self.balance = {'USDT': 10000, 'ETH': 0}
            self.position = None
        else:
            print("Mode : TRADING RÉEL")
            if not API_KEY or not API_SECRET:
                print("ERREUR: Variables non définies!")
                exit(1)
            
            self.exchange = ccxt.gateio({
                'apiKey': API_KEY,
                'secret': API_SECRET,
                'enableRateLimit': True,
                'options': {'createMarketBuyOrderRequiresPrice': False},
            })
            
            try:
                self.exchange.fetch_time()
                print("Connexion Gate.io OK!")
            except Exception as e:
                print(f"Erreur: {e}")
            
            self.balance = self.get_real_balance()
            eth = float(self.balance.get('ETH', 0))
            if eth > 0:
                self.position = {'side': 'long', 'entry': 0, 'amount': eth}
                print(f"Position ETH: {eth}")
            else:
                self.position = None
    
    def get_real_balance(self):
        try:
            balance = self.exchange.fetch_balance()
            usdt = float(balance.get('total', {}).get('USDT', 0) or 0)
            eth = float(balance.get('total', {}).get('ETH', 0) or 0)
            return {'USDT': usdt, 'ETH': eth}
        except:
            return {'USDT': 0, 'ETH': 0}
    
    def get_price(self):
        try:
            ticker = self.exchange.fetch_ticker(SYMBOL)
            return float(ticker.get('last') or ticker.get('close'))
        except:
            return None
    
    def get_data(self, limit=100):
        try:
            ohlcv = self.exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=limit)
            if not ohlcv or len(ohlcv) < 26:
                return None
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df['close'] = pd.to_numeric(df['close'], errors='coerce')
            return df.dropna()
        except:
            return None
    
    def calculate_rsi(self, data, period=14):
        try:
            closes = data['close'].values
            deltas = [float(closes[i]) - float(closes[i-1]) for i in range(1, len(closes))]
            gains = [max(d, 0) for d in deltas[-period:]]
            losses = [abs(min(d, 0)) for d in deltas[-period:]]
            avg_gain = sum(gains) / period
            avg_loss = sum(losses) / period
            if avg_loss == 0:
                return 100.0
            return 100 - (100 / (1 + avg_gain / avg_loss))
        except:
            return 50.0
    
    def calculate_macd(self, data):
        try:
            closes = data['close'].values
            ema12 = self._ema(closes, 12)
            ema26 = self._ema(closes, 26)
            macd = ema12 - ema26
            signal = self._ema([macd]*9, 9)
            return float(macd), float(signal)
        except:
            return 0.0, 0.0
    
    def _ema(self, values, period):
        values = [float(v) for v in values[-period:]]
        k = 2 / (period + 1)
        ema = sum(values) / period
        for v in values[1:]:
            ema = v * k + ema * (1 - k)
        return ema
    
    def calculate_profitability(self, current_price):
        try:
            if not self.position:
                return True, 0.0, {}
            
            entry = float(self.position.get('entry', 0))
            amount = float(self.position.get('amount', 0))
            
            if entry == 0 or amount == 0:
                return True, 0.0, {}
            
            cost = entry * (1 + TRADING_FEE)
            net = current_price * (1 - TRADING_FEE)
            profit_pct = ((net - cost) / cost) * 100
            profit_usdt = (net - cost) * amount
            is_profitable = profit_pct >= MIN_PROFIT_THRESHOLD
            
            return is_profitable, float(profit_pct), {'profit_usdt': profit_usdt}
        except:
            return True, 0.0, {}
    
    def should_buy(self, data):
        rsi = self.calculate_rsi(data)
        macd, signal = self.calculate_macd(data)
        return rsi < RSI_BUY_THRESHOLD or (macd > signal and rsi < 50)
    
    def should_sell(self, data):
        rsi = self.calculate_rsi(data)
        macd, signal = self.calculate_macd(data)
        
        price = self.get_price()
        if price is None:
            return False
        
        is_profitable, profit_pct, details = self.calculate_profitability(price)
        
        # Take-Profit si >= 1.5% ET profit positif
        if profit_pct >= TAKE_PROFIT_THRESHOLD and profit_pct > 0:
            print(f"  -> TAKE-PROFIT! Vente auto à {profit_pct:.2f}%")
            return True
        
        # Sinon vendre que si profit >= minimum ET positif
        if is_profitable and profit_pct >= MIN_PROFIT_THRESHOLD:
            print(f"  -> Vente RENTABLE: {profit_pct:.2f}%")
            return True
        else:
            print(f"  -> Vente NON RENTABLE: {profit_pct:.2f}% - Min: {MIN_PROFIT_THRESHOLD}%")
        
        # Signaux techniques seulement si profit positif
        technical_sell = rsi > 60 or (macd < signal and rsi > 50)
        
        if technical_sell and profit_pct > 0:
            print(f"  -> Signal tech. Vente: {profit_pct:.2f}%")
            return True
        
        return False
    
    def buy(self):
        try:
            if not PAPER_MODE:
                self.balance = self.get_real_balance()
            
            price = self.get_price()
            if price is None:
                return
            
            total = float(self.balance.get('USDT', 0)) - MIN_USDT_RESERVE
            available = total * (MAX_USDT_PERCENT / 100)
            
            if available > 5:
                amount = (available / price) * (1 - TRADING_FEE)
                if amount * price >= 7:
                    if PAPER_MODE:
                        self.balance['USDT'] -= available
                        self.balance['ETH'] += amount
                        self.position = {'side': 'long', 'entry': price, 'amount': amount}
                        print(f"ACHAT simulé: {amount:.5f} ETH à ${price}")
                    else:
                        order = self.exchange.create_order(SYMBOL, 'market', 'buy', available)
                        print(f"ACHAT réel: {amount:.5f} ETH à ${price} ({MAX_USDT_PERCENT}%)")
                        self.position = {'side': 'long', 'entry': price, 'amount': amount}
        except Exception as e:
            print(f"Erreur achat: {e}")
    
    def sell(self):
        try:
            if not PAPER_MODE:
                self.balance = self.get_real_balance()
            
            eth = float(self.balance.get('ETH', 0))
            if self.position and eth >= 0.001:
                price = self.get_price()
                if price is None:
                    return
                
                is_profitable, profit_pct, details = self.calculate_profitability(price)
                
                if not is_profitable or profit_pct < 0:
                    print(f"  -> Vente ANNULÉE: {profit_pct:.2f}%")
                    return
                
                if eth * price >= 7:
                    if PAPER_MODE:
                        self.balance['ETH'] = 0
                        self.balance['USDT'] += eth * price * (1 - TRADING_FEE)
                        print(f"VENTE simulée: {eth:.5f} ETH à ${price}")
                        self.position = None
                    else:
                        order = self.exchange.create_order(SYMBOL, 'market', 'sell', eth)
                        print(f"VENTE réelle: {eth:.5f} ETH à ${price}")
                        self.position = None
        except Exception as e:
            print(f"Erreur vente: {e}")
    
    def run(self):
        print(f"\n===== BOT GATE.IO ETH =====")
        print(f"Allocation: {MAX_USDT_PERCENT}% | Profit: {MIN_PROFIT_THRESHOLD}% | Take-Profit: {TAKE_PROFIT_THRESHOLD}%")
        print(f"====================================\n")
        
        while True:
            try:
                if not PAPER_MODE:
                    self.balance = self.get_real_balance()
                
                data = self.get_data()
                if data is not None:
                    price = self.get_price()
                    if price is not None:
                        print(f"\n{datetime.now().strftime('%H:%M:%S')} | Prix: ${price:,.2f}")
                        print(f"  USDT: {float(self.balance.get('USDT', 0)):.2f} | ETH: {float(self.balance.get('ETH', 0)):.5f}")
                        
                        if self.position is None:
                            if self.should_buy(data):
                                print("  -> ACHAT!")
                                self.buy()
                        else:
                            if self.should_sell(data):
                                print("  -> VENTE!")
                                self.sell()
                        
                        rsi = self.calculate_rsi(data)
                        macd, signal = self.calculate_macd(data)
                        print(f"  RSI: {rsi:.1f} | MACD: {macd:.2f}")
                
                time.sleep(900)
            except KeyboardInterrupt:
                print("\nBot arrêté!")
                break
            except Exception as e:
                print(f"Erreur: {e}")
                time.sleep(60)

def run_web_server():
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    print(f"Web server port {port}")
    server.serve_forever()

if __name__ == '__main__':
    threading.Thread(target=run_web_server, daemon=True).start()
    SimpleBot().run()