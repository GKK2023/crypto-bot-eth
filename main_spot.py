# CryptoBot - Spot Trading Bot ETH/USDT
# Version avec persistance du prix d'achat

import os
import sys
import ccxt
import time
import json
import pandas as pd
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

# Configuration
SYMBOL = 'ETH/USDT'
TIMEFRAME = '15m'
PAPER_MODE = False

# Clés API Gate.io
API_KEY = os.getenv('GATEIO_API_KEY', '')
API_SECRET = os.getenv('GATEIO_API_SECRET', '')

# Frais Gate.io
TRADING_FEE = 0.001
TOTAL_FEES = 0.002

# Solde minimum à garder en USDT
MIN_USDT_RESERVE = 5

# Pourcentage du solde à utiliser
MAX_USDT_PERCENT = 40

# Seuil de profit minimum NET
MIN_PROFIT_THRESHOLD = 0.5

# Take-Profit automatique
TAKE_PROFIT_THRESHOLD = 2.0

# Seuil RSI pour achat
RSI_BUY_THRESHOLD = 40

# Seuil minimum pour une vraie position
MIN_POSITION_THRESHOLD = 0.0001

# Fichier pour sauvegarder le prix d'achat
ENTRY_PRICE_FILE = 'entry_price.json'


class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        response = "<html><body><h1>CryptoBot</h1><p>Bot ETH/USDT Active</p></body></html>"
        self.wfile.write(response.encode())

    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()


class SimpleBot:
    
    def save_entry_price(self, entry_price, amount):
        """Sauvegarde le prix d'achat dans un fichier"""
        try:
            data = {
                'entry_price': entry_price,
                'amount': amount,
                'timestamp': datetime.now().isoformat()
            }
            with open(ENTRY_PRICE_FILE, 'w') as f:
                json.dump(data, f)
            print(f"[DEBUG] Prix d'achat sauvegarde: ${entry_price:.4f}")
        except Exception as e:
            print(f"[DEBUG] Erreur sauvegarde: {e}")
    
    def load_entry_price(self):
        """Charge le prix d'achat depuis un fichier"""
        try:
            if os.path.exists(ENTRY_PRICE_FILE):
                with open(ENTRY_PRICE_FILE, 'r') as f:
                    data = json.load(f)
                entry_price = data.get('entry_price', 0)
                amount = data.get('amount', 0)
                if entry_price > 0:
                    print(f"[DEBUG] Prix d'achat charge depuis fichier: ${entry_price:.4f}")
                    return entry_price, amount
        except Exception as e:
            print(f"[DEBUG] Erreur chargement: {e}")
        return None, 0
    
    def clear_entry_price(self):
        """Efface le fichier du prix d'achat (après vente)"""
        try:
            if os.path.exists(ENTRY_PRICE_FILE):
                os.remove(ENTRY_PRICE_FILE)
                print("[DEBUG] Fichier prix d'achat efface")
        except:
            pass
    
    def __init__(self):
        print(f"[DEBUG] Bot ETH - __init__ appele")
        
        if PAPER_MODE:
            self.exchange = ccxt.gateio({'enableRateLimit': True})
            self.balance = {'USDT': 10000, 'ETH': 0}
            self.position = None
        else:
            if not API_KEY or not API_SECRET:
                print("ERREUR: Les variables d'environnement ne sont pas definies!")
                sys.exit(1)
            try:
                self.exchange = ccxt.gateio({
                    'apiKey': API_KEY,
                    'secret': API_SECRET,
                    'enableRateLimit': True,
                    'options': {'createMarketBuyOrderRequiresPrice': False},
                })
                self.exchange.fetch_time()
                print("Connexion a Gate.io ETH reussie!")
            except Exception as e:
                print(f"Erreur de connexion: {e}")
                sys.exit(1)

        self.balance = self.get_real_balance()
        print(f"[DEBUG] Solde: USDT={self.balance.get('USDT', 0)}, ETH={self.balance.get('ETH', 0)}")

        eth_balance = float(self.balance.get('ETH', 0))
        if eth_balance >= MIN_POSITION_THRESHOLD:
            # ETAPE 1: Essayer de charger depuis le fichier
            entry_price, saved_amount = self.load_entry_price()
            
            if entry_price and saved_amount > 0:
                self.position = {'side': 'long', 'entry': entry_price, 'amount': eth_balance}
                print(f"Position ETH (depuis fichier): {eth_balance} @ ${entry_price:.4f}")
            else:
                # ETAPE 2: Chercher dans l'historique des trades
                entry_price = self.get_entry_price_from_trades()
                if not entry_price:
                    entry_price = self.get_entry_price_from_orders()
                
                if entry_price:
                    self.position = {'side': 'long', 'entry': entry_price, 'amount': eth_balance}
                    self.save_entry_price(entry_price, eth_balance)
                    print(f"Position ETH (depuis trades): {eth_balance} @ ${entry_price:.4f}")
                else:
                    # ETAPE 3: Utiliser le prix actuel
                    current_price = self.get_price()
                    if current_price:
                        self.position = {'side': 'long', 'entry': current_price, 'amount': eth_balance}
                        self.save_entry_price(current_price, eth_balance)
                        print(f"Position ETH (prix actuel): {eth_balance} @ ${current_price:.4f}")
                    else:
                        self.position = None
        else:
            print(f"Pas de position ETH")
            self.position = None


    def get_entry_price_from_orders(self):
        try:
            orders = self.exchange.fetch_closed_orders(SYMBOL, limit=20)
            buy_orders = [o for o in orders if o['side'] == 'buy' and o['status'] == 'closed']
            if buy_orders:
                # Prendre le plus ancien ordre d'achat
                buy_orders.sort(key=lambda x: x.get('timestamp', 0))
                for order in buy_orders:
                    price = order.get('average') or order.get('price')
                    if price and float(price) > 0:
                        print(f"[DEBUG] Prix achat (orders): ${float(price):.4f}")
                        return float(price)
            return None
        except Exception as e:
            print(f"[DEBUG] Erreur ordres: {e}")
            return None


    def get_entry_price_from_trades(self):
        try:
            trades = self.exchange.fetch_my_trades(SYMBOL, limit=100)
            if not trades:
                return None
            
            # Filtrer les achats et trier par timestamp (plus ancien en premier)
            buy_trades = [t for t in trades if t['side'] == 'buy']
            if buy_trades:
                buy_trades.sort(key=lambda x: x.get('timestamp', 0))
                first_buy = buy_trades[0]
                price = first_buy.get('price') or first_buy.get('average')
                
                if price and float(price) > 0:
                    print(f"[DEBUG] Prix achat (trades): ${float(price):.4f}")
                    return float(price)
            
            return None
        except Exception as e:
            print(f"[DEBUG] Erreur trades: {e}")
            return None


    def get_real_balance(self):
        try:
            balance = self.exchange.fetch_balance()
            usdt_balance = 0
            eth_balance = 0
            if isinstance(balance, dict):
                total = balance.get('total', {})
                if isinstance(total, dict):
                    usdt_balance = float(total.get('USDT', 0) or 0)
                    eth_balance = float(total.get('ETH', 0) or 0)
            return {'USDT': usdt_balance, 'ETH': eth_balance}
        except Exception as e:
            print(f"Erreur solde: {e}")
            return {'USDT': 0, 'ETH': 0}


    def get_price(self):
        try:
            ticker = self.exchange.fetch_ticker(SYMBOL)
            last = ticker.get('last')
            if last is None:
                last = ticker.get('close')
            return float(last) if last is not None else None
        except Exception as e:
            print(f"Erreur prix: {e}")
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
        except Exception as e:
            print(f"Erreur donnees: {e}")
            return None


    def calculate_rsi(self, data, period=14):
        try:
            if data is None or len(data) < period:
                return 50.0
            
            closes = data['close'].values
            if len(closes) < period:
                return 50.0
            
            deltas = []
            for i in range(1, len(closes)):
                deltas.append(float(closes[i]) - float(closes[i-1]))
            
            gains = [max(d, 0) for d in deltas[-period:]]
            losses = [abs(min(d, 0)) for d in deltas[-period:]]
            
            avg_gain = sum(gains) / period
            avg_loss = sum(losses) / period
            
            if avg_loss == 0:
                return 100.0
            
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            return float(rsi)
        except Exception as e:
            return 50.0


    def calculate_macd(self, data):
        try:
            if data is None or len(data) < 26:
                return 0.0, 0.0
            
            closes = data['close'].values
            if len(closes) < 26:
                return 0.0, 0.0
            
            ema12 = self._calculate_ema(closes, 12)
            ema26 = self._calculate_ema(closes, 26)
            macd = ema12 - ema26
            signal = self._calculate_ema([macd] * 9, 9)
            return float(macd), float(signal)
        except Exception as e:
            return 0.0, 0.0

    def _calculate_ema(self, values, period):
        try:
            values = [float(v) for v in values[-period:]]
            multiplier = 2 / (period + 1)
            ema = sum(values) / period
            for value in values[1:]:
                ema = (value * multiplier) + (ema * (1 - multiplier))
            return ema
        except:
            return values[-1] if len(values) > 0 else 0


    def calculate_profitability(self, current_price):
        try:
            if not self.position:
                return True, 0.0, {}
            
            entry_price = float(self.position.get('entry', 0))
            amount_eth = float(self.position.get('amount', 0))
            
            if entry_price == 0 or amount_eth == 0:
                print(f"[DEBUG] Prix d'achat manquant!")
                return True, 0.0, {}
            
            break_even_price = entry_price * (1 + TOTAL_FEES)
            target_price = break_even_price * (1 + MIN_PROFIT_THRESHOLD / 100)
            
            profit_pct = ((current_price - entry_price) / entry_price) * 100
            profit_usdt = (current_price - entry_price) * amount_eth
            
            is_profitable = current_price > target_price
            
            return is_profitable, float(profit_pct), {
                'entry_price': entry_price,
                'current_price': current_price,
                'target_price': target_price,
                'profit_usdt': profit_usdt
            }
        except Exception as e:
            print(f"Erreur profit: {e}")
            return True, 0.0, {}


    def should_buy(self, data):
        try:
            rsi = self.calculate_rsi(data)
            if rsi < RSI_BUY_THRESHOLD:
                return True
            return False
        except Exception as e:
            return False


    def should_sell(self, data):
        try:
            current_price = self.get_price()
            if current_price is None:
                return False
            
            is_profitable, profit_pct, details = self.calculate_profitability(current_price)
            
            if profit_pct >= MIN_PROFIT_THRESHOLD and profit_pct > 0:
                print(f" -> Vente RENTABLE: {profit_pct:.2f}% (+{details.get('profit_usdt', 0):.2f}$)")
                return True
            
            if not is_profitable:
                target = details.get('target_price', 0)
                entry = details.get('entry_price', 0)
                print(f" -> En attente: Profit: {profit_pct:.2f}% | Achat: ${entry:.2f} | Cible: ${target:.2f}")
            else:
                print(f" -> En attente: Profit: {profit_pct:.2f}% | Min: {MIN_PROFIT_THRESHOLD}%")
            
            return False
        except Exception as e:
            print(f"Erreur sell: {e}")
            return False


    def buy(self):
        try:
            if not PAPER_MODE:
                self.balance = self.get_real_balance()
            
            price = self.get_price()
            if price is None:
                return
            
            total_usdt = float(self.balance.get('USDT', 0))
            usdt_to_use = (total_usdt - MIN_USDT_RESERVE) * (MAX_USDT_PERCENT / 100)
            
            if usdt_to_use > 5:
                amount_before_fee = usdt_to_use / price
                amount_after_fee = amount_before_fee * (1 - TRADING_FEE)
                
                if amount_after_fee * price >= 7:
                    amount = round(amount_after_fee, 4)
                    
                    if PAPER_MODE:
                        self.balance['USDT'] -= usdt_to_use
                        self.balance['ETH'] += amount
                        self.position = {'side': 'long', 'entry': price, 'amount': amount}
                        self.save_entry_price(price, amount)
                        print(f"ACHAT simule: {amount:.4f} ETH @ ${price}")
                    else:
                        order = self.exchange.create_order(SYMBOL, 'market', 'buy', usdt_to_use)
                        print(f"ACHAT reel: {amount:.4f} ETH @ ${price}")
                        
                        # Recupérer le prix d'achat réel depuis l'ordre
                        filled = order.get('fills', [])
                        if filled:
                            total_cost = sum(float(f.get('cost', 0)) for f in filled)
                            total_amount = sum(float(f.get('amount', 0)) for f in filled)
                            if total_amount > 0:
                                real_entry = total_cost / total_amount
                                self.position = {'side': 'long', 'entry': real_entry, 'amount': amount}
                                self.save_entry_price(real_entry, amount)
                                print(f"[DEBUG] Prix d'achat reel: ${real_entry:.4f}")
                        else:
                            self.position = {'side': 'long', 'entry': price, 'amount': amount}
                            self.save_entry_price(price, amount)
        except Exception as e:
            print(f"Erreur achat: {e}")


    def sell(self):
        try:
            if not PAPER_MODE:
                self.balance = self.get_real_balance()
            
            eth_balance = float(self.balance.get('ETH', 0))
            
            if eth_balance >= MIN_POSITION_THRESHOLD:
                price = self.get_price()
                if price is None:
                    return
                
                is_profitable, profit_pct, details = self.calculate_profitability(price)
                
                if not is_profitable:
                    print(f" -> Vente ANNULEE: Non rentable")
                    return
                
                amount = eth_balance
                
                if amount * price >= 7:
                    if PAPER_MODE:
                        self.balance['ETH'] = 0
                        self.balance['USDT'] += amount * price * (1 - TRADING_FEE)
                        print(f"VENTE simulee: {amount:.4f} ETH @ ${price}")
                        self.position = None
                        self.clear_entry_price()
                    else:
                        order = self.exchange.create_order(SYMBOL, 'market', 'sell', amount)
                        print(f"VENTE reelle: {amount:.4f} ETH @ ${price}")
                        self.position = None
                        self.clear_entry_price()
        except Exception as e:
            print(f"Erreur vente: {e}")


    def run(self):
        print(f"\n===== BOT ETH/USDT - 3 MINUTES =====")
        print(f"Paire: {SYMBOL}")
        print(f"RSI achat: {RSI_BUY_THRESHOLD}")
        print(f"Allocation: {MAX_USDT_PERCENT}%")
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
                        print(f" USDT: {float(self.balance.get('USDT', 0)):.2f} | ETH: {float(self.balance.get('ETH', 0)):.4f}")
                        
                        eth_balance = float(self.balance.get('ETH', 0))
                        
                        if self.position is None:
                            if self.should_buy(data):
                                print(" -> Signal ACHAT!")
                                self.buy()
                        else:
                            if eth_balance < MIN_POSITION_THRESHOLD:
                                print(f" -> Dust ignore: {eth_balance:.6f} ETH")
                                self.position = None
                                self.clear_entry_price()
                            else:
                                if self.should_sell(data):
                                    print(" -> Signal VENTE!")
                                    self.sell()
                        
                        rsi = self.calculate_rsi(data)
                        macd, signal = self.calculate_macd(data)
                        print(f" RSI: {rsi:.1f} | MACD: {macd:.2f}")
                
                time.sleep(180)
            except KeyboardInterrupt:
                print("\nBot arrete!")
                break
            except Exception as e:
                print(f"Erreur: {e}")
                time.sleep(60)


def run_web_server():
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), SimpleHandler)
    print(f"Serveur web sur port {port}")
    server.serve_forever()


if __name__ == '__main__':
    import threading
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    bot = SimpleBot()
    bot.run()