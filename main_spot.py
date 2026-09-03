# CryptoBot ETH/USDT - Spot Trading v2
import os, sys, ccxt, time, pandas as pd
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

SYMBOL = 'ETH/USDT'
TIMEFRAME = '15m'
PAPER_MODE = False
API_KEY = os.getenv('GATEIO_API_KEY', '')
API_SECRET = os.getenv('GATEIO_API_SECRET', '')
TRADING_FEE = 0.001
TOTAL_FEES = 0.002
MIN_USDT_RESERVE = 5
MAX_USDT_PERCENT = 40
MIN_PROFIT_THRESHOLD = 0.5
TAKE_PROFIT_THRESHOLD = 0.5
TRAILING_STOP_PCT = 0.3
RSI_BUY_THRESHOLD = 40
MACD_CONFIRM = True
COOLDOWN_CYCLES = 5
MIN_POSITION_THRESHOLD = 0.001


class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        price = getattr(self.server, 'last_price', 'N/A')
        rsi = getattr(self.server, 'last_rsi', 'N/A')
        profit = getattr(self.server, 'last_profit', 'N/A')
        pos = getattr(self.server, 'position_status', 'Aucun')
        resp = f"<!DOCTYPE html><html><head><title>CryptoBot ETH</title><meta name='viewport' content='width=device-width'><style>body{{font-family:Arial;padding:20px;background:#1a1a2e;color:#eee}}h1{{color:#a855f7}}.card{{background:#16213e;padding:15px;border-radius:10px;margin:10px 0}}.price{{font-size:2em;color:#c084fc}}.label{{color:#888;font-size:0.8em}}.profit{{color:#00ff88}}.pos{{color:#ffaa00}}</style></head><body><h1>🤖 Bot ETH/USDT — v2</h1><div class='card'><div class='label'>Prix actuel</div><div class='price'>${price}</div></div><div class='card'><div class='label'>Position</div><div class='pos'>{pos}</div></div><div class='card'><div class='label'>RSI</div><div>{rsi}</div></div><div class='card'><div class='label'>Dernier profit</div><div class='profit'>{profit}</div></div></body></html>"
        self.wfile.write(resp.encode())
    def do_HEAD(self):
        self.send_response(200); self.send_header('Content-type', 'text/html'); self.end_headers()
    def log_message(self, format, *args): pass


class SimpleBot:
    def __init__(self):
        print(f"[DEBUG] Bot ETH - __init__ appelé")
        if PAPER_MODE:
            self.exchange = ccxt.gate({'enableRateLimit': True})
            self.balance = {'USDT': 10000, 'ETH': 0}; self.position = None
        else:
            if not API_KEY or not API_SECRET: print("ERREUR: API non définies!"); sys.exit(1)
            self.exchange = ccxt.gate({'apiKey': API_KEY, 'secret': API_SECRET, 'enableRateLimit': True, 'options': {'createMarketBuyOrderRequiresPrice': False}})
            self.exchange.fetch_time()
            print("Connexion à Gate.io ETH réussie!")
        self.balance = self.get_real_balance()
        print(f"[DEBUG] Solde: USDT={self.balance.get('USDT', 0):.2f}, ETH={self.balance.get('ETH', 0):.6f}")
        self.position = None; self.cooldown_remaining = 0; self.last_sell_profit_pct = 0.0
        self.peak_price_since_buy = 0.0; self.prev_macd = None; self.prev_signal = None
        self._detect_existing_position()

    def _detect_existing_position(self):
        eth_balance = float(self.balance.get('ETH', 0))
        if eth_balance < MIN_POSITION_THRESHOLD: print(f"Pas de position ETH"); self.position = None; return
        entry_price = self.get_entry_price_from_trades()
        if not entry_price: entry_price = self.get_entry_price_from_orders()
        if not entry_price: entry_price = self.get_price()
        if entry_price:
            self.position = {'side': 'long', 'entry': float(entry_price), 'amount': eth_balance}
            self.peak_price_since_buy = float(entry_price)
            print(f"Position ETH: {eth_balance:.6f} @ ${entry_price:.2f}")
        else: self.position = None

    def get_entry_price_from_orders(self):
        try:
            orders = self.exchange.fetch_closed_orders(SYMBOL, limit=10)
            buy_orders = [o for o in orders if o['side'] == 'buy' and o['status'] == 'closed']
            if buy_orders:
                last_buy = sorted(buy_orders, key=lambda x: x['timestamp'], reverse=True)[0]
                price = last_buy.get('average') or last_buy.get('price')
                if price and float(price) > 0: print(f"[DEBUG] Prix achat (orders): ${float(price):.2f}"); return float(price)
            return None
        except Exception as e: print(f"[DEBUG] Erreur ordres: {e}"); return None

    def get_entry_price_from_trades(self):
        try:
            trades = self.exchange.fetch_my_trades(SYMBOL, limit=30)
            if not trades: return None
            buy_trades = [t for t in trades if t['side'] == 'buy']
            if not buy_trades: return None
            last_buy = sorted(buy_trades, key=lambda x: x['timestamp'], reverse=True)[0]
            price = last_buy.get('price') or last_buy.get('average')
            cost = last_buy.get('cost', 0)
            if price and float(price) > 0 and float(cost) > 5:
                print(f"[DEBUG] Prix achat (trades): ${float(price):.2f} | Cost: ${float(cost):.2f}"); return float(price)
            return None
        except Exception as e: print(f"[DEBUG] Erreur trades: {e}"); return None

    def get_real_balance(self):
        try:
            balance = self.exchange.fetch_balance()
            if isinstance(balance, dict):
                total = balance.get('total', {})
                if isinstance(total, dict): return {'USDT': float(total.get('USDT', 0) or 0), 'ETH': float(total.get('ETH', 0) or 0)}
            return {'USDT': 0, 'ETH': 0}
        except: return {'USDT': 0, 'ETH': 0}

    def get_price(self):
        try:
            ticker = self.exchange.fetch_ticker(SYMBOL)
            last = ticker.get('last') or ticker.get('close')
            return float(last) if last else None
        except: return None

    def get_data(self, limit=100):
        try:
            ohlcv = self.exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=limit)
            if not ohlcv or len(ohlcv) < 26: return None
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df['close'] = pd.to_numeric(df['close'], errors='coerce')
            return df.dropna()
        except: return None

    def calculate_rsi(self, data, period=14):
        try:
            if data is None or len(data) < period + 1: return 50.0
            closes = data['close'].values
            deltas = [float(closes[i]) - float(closes[i-1]) for i in range(1, len(closes))]
            gains = [max(d, 0) for d in deltas[-period:]]
            losses = [abs(min(d, 0)) for d in deltas[-period:]]
            avg_gain = sum(gains) / period; avg_loss = sum(losses) / period
            if avg_loss == 0: return 100.0
            return float(100 - (100 / (1 + avg_gain / avg_loss)))
        except: return 50.0

    def calculate_macd(self, data):
        try:
            if data is None or len(data) < 26: return 0.0, 0.0
            closes = data['close'].values
            macd_series = []
            for i in range(26, len(closes)):
                e12 = self._ema(closes[:i+1], 12); e26 = self._ema(closes[:i+1], 26)
                macd_series.append(e12 - e26)
            macd = macd_series[-1] if macd_series else 0
            signal = self._ema(macd_series[-9:] if len(macd_series) >= 9 else macd_series, 9)
            return float(macd), float(signal)
        except: return 0.0, 0.0

    def _ema(self, values, period):
        try:
            values = [float(v) for v in values[-period:]]
            if len(values) < period: return values[-1] if values else 0
            multiplier = 2 / (period + 1)
            ema = sum(values[:period]) / period
            for v in values[period:]: ema = (v * multiplier) + (ema * (1 - multiplier))
            return ema
        except: return values[-1] if len(values) > 0 else 0

    def get_indicators(self, data):
        rsi = self.calculate_rsi(data)
        macd, signal = self.calculate_macd(data)
        histogram = macd - signal
        if self.prev_macd is not None:
            prev_histogram = self.prev_macd - (self.prev_signal or 0)
            macd_improving = histogram > prev_histogram
        else: macd_improving = True
        self.prev_macd = macd; self.prev_signal = signal
        return rsi, macd, signal, histogram, macd_improving

    def calculate_profitability(self, current_price):
        if not self.position: return True, 0.0, {}
        entry = float(self.position.get('entry', 0)); amount = float(self.position.get('amount', 0))
        if entry == 0 or amount == 0:
            entry = self.get_entry_price_from_trades()
            if entry: self.position['entry'] = entry; self.peak_price_since_buy = max(self.peak_price_since_buy, entry)
            else: return True, 0.0, {}
        break_even = entry * (1 + TOTAL_FEES)
        target_price = break_even * (1 + MIN_PROFIT_THRESHOLD / 100)
        profit_pct = ((current_price - entry) / entry) * 100
        profit_usdt = (current_price - entry) * amount * (1 - TRADING_FEE)
        is_profitable = current_price >= target_price
        return is_profitable, float(profit_pct), {'entry_price': entry, 'current_price': current_price, 'target_price': target_price, 'break_even': break_even, 'profit_usdt': profit_usdt, 'amount': amount}

    def should_buy(self, data):
        rsi, macd, signal, histogram, macd_improving = self.get_indicators(data)
        if rsi >= RSI_BUY_THRESHOLD: return False
        if MACD_CONFIRM and not macd_improving:
            print(f"  -> RSI OK ({rsi:.1f} < {RSI_BUY_THRESHOLD}) mais MACD se dégrade encore (histogramme: {histogram:.3f}, en baisse)")
            return False
        return True

    def should_sell(self, data):
        current_price = self.get_price()
        if current_price is None: return False
        is_profitable, profit_pct, details = self.calculate_profitability(current_price)
        entry = details.get('entry_price', 0); target = details.get('target_price', 0)
        if current_price > self.peak_price_since_buy: self.peak_price_since_buy = current_price
        trailing_trigger = self.peak_price_since_buy * (1 - TRAILING_STOP_PCT / 100)
        if self.position:
            if is_profitable and profit_pct > 0:
                print(f"  -> ✅ TAKE-PROFIT: {profit_pct:.2f}% (+{details.get('profit_usdt', 0):.2f}$ sur {details.get('amount', 0):.6f} ETH)")
                self.last_sell_profit_pct = profit_pct; return True
            if current_price <= trailing_trigger and self.peak_price_since_buy > entry * 1.01:
                drawdown = (self.peak_price_since_buy - current_price) / self.peak_price_since_buy * 100
                print(f"  -> 🛡️ TRAILING STOP: Prix=${current_price:.2f} | Peak=${self.peak_price_since_buy:.2f} | Retrait={drawdown:.1f}%")
                self.last_sell_profit_pct = profit_pct; return True
            trailing_msg = f" | Trailing peak: ${self.peak_price_since_buy:.2f}" if self.peak_price_since_buy > entry else ""
            print(f"  -> En attente: {profit_pct:+.2f}% | Achat: ${entry:.2f} | Cible: ${target:.2f}{trailing_msg}")
        return False

    def buy(self):
        try:
            self.balance = self.get_real_balance()
            price = self.get_price()
            if price is None: return
            total_usdt = float(self.balance.get('USDT', 0))
            usdt_to_use = (total_usdt - MIN_USDT_RESERVE) * (MAX_USDT_PERCENT / 100)
            if usdt_to_use <= 5: print(f"  -> Solde insuffisant"); return
            amount_after_fee = (usdt_to_use / price) * (1 - TRADING_FEE)
            if amount_after_fee * price < 7: print(f"  -> Montant trop petit"); return
            amount = round(amount_after_fee, 4)
            if PAPER_MODE:
                self.balance['USDT'] -= usdt_to_use; self.balance['ETH'] += amount
                self.position = {'side': 'long', 'entry': price, 'amount': amount}; self.peak_price_since_buy = price
                print(f"  ACHAT simulé: {amount:.4f} ETH @ ${price}")
            else:
                order = self.exchange.create_order(SYMBOL, 'market', 'buy', usdt_to_use)
                fill_price = order.get('average') or order.get('price') or price
                filled_amount = order.get('filled') or order.get('amount') or amount
                print(f"  ACHAT RÉEL: {filled_amount:.6f} ETH @ ${float(fill_price):.2f}")
                self.position = {'side': 'long', 'entry': float(fill_price), 'amount': float(filled_amount)}
                self.peak_price_since_buy = float(fill_price)
                self.balance = self.get_real_balance()
        except Exception as e: print(f"Erreur achat: {e}")

    def sell(self):
        try:
            self.balance = self.get_real_balance()
            eth_balance = float(self.balance.get('ETH', 0))
            if eth_balance < MIN_POSITION_THRESHOLD: self.position = None; return
            price = self.get_price()
            if price is None: return
            is_profitable, profit_pct, details = self.calculate_profitability(price)
            if not is_profitable: print(f"  -> Vente ANNULÉE"); return
            amount = eth_balance
            if amount * price < 7: print(f"  -> Montant trop petit"); return
            if PAPER_MODE:
                self.balance['ETH'] = 0; self.balance['USDT'] += amount * price * (1 - TRADING_FEE)
                self.position = None; self.balance = self.get_real_balance()
            else:
                self.exchange.create_order(SYMBOL, 'market', 'sell', amount)
                print(f"  VENTE RÉELLE: {amount:.6f} ETH @ ${price}")
                self.position = None; self.balance = self.get_real_balance(); self.peak_price_since_buy = 0.0
            self.cooldown_remaining = COOLDOWN_CYCLES
            print(f"  ⏳ Cooldown: {COOLDOWN_CYCLES} cycles ({COOLDOWN_CYCLES * 3} min)")
        except Exception as e: print(f"Erreur vente: {e}")

    def run(self):
        print(f"\n===== BOT ETH/USDT v2 =====")
        print(f"RSI achat: < {RSI_BUY_THRESHOLD} | Take-Profit: {MIN_PROFIT_THRESHOLD}% | Trailing: {TRAILING_STOP_PCT}%")
        print(f"MACD: {MACD_CONFIRM} | Cooldown: {COOLDOWN_CYCLES * 3} min | Allocation: {MAX_USDT_PERCENT}%")
        print(f"============================\n")
        cycle = 0
        while True:
            try:
                cycle += 1; self.balance = self.get_real_balance(); data = self.get_data()
                if data is not None:
                    price = self.get_price()
                    if price is not None:
                        now = datetime.now().strftime('%H:%M:%S')
                        usdt_bal = float(self.balance.get('USDT', 0)); eth_bal = float(self.balance.get('ETH', 0))
                        rsi, macd, signal, histogram, macd_improving = self.get_indicators(data)
                        print(f"\n{now} | Prix: ${price:,.2f} | RSI: {rsi:.1f} | MACD: {macd:.2f}/{signal:.2f}")
                        print(f" USDT: {usdt_bal:.2f} | ETH: {eth_bal:.6f} | Last profit: {self.last_sell_profit_pct:+.2f}%")
                        if self.position is None:
                            if self.cooldown_remaining > 0:
                                print(f"  ⏳ Cooldown: {self.cooldown_remaining} cycles"); self.cooldown_remaining -= 1
                            else:
                                if self.should_buy(data): print(f"  -> ✅ SIGNAL ACHAT (RSI={rsi:.1f})"); self.buy()
                        else:
                            if eth_bal < MIN_POSITION_THRESHOLD: self.position = None
                            elif self.should_sell(data): print(f"  -> ✅ SIGNAL VENTE!"); self.sell()
                time.sleep(180)
            except KeyboardInterrupt: print("\nBot arrêté!"); break
            except Exception as e: print(f"Erreur: {e}"); time.sleep(60)


def run_web_server(port):
    try:
        server = HTTPServer(('0.0.0.0', port), SimpleHandler)
        print(f"🌐 Serveur web: http://0.0.0.0:{port}"); server.serve_forever()
    except Exception as e: print(f"Erreur serveur: {e}")


if __name__ == '__main__':
    import threading
    port = int(os.environ.get('PORT', 10000))
    web_thread = threading.Thread(target=run_web_server, args=(port,), daemon=True)
    web_thread.start()
    bot = SimpleBot()
    SimpleHandler.server_instance = bot
    bot.run()
