from flask import Flask, jsonify, request
import websocket
import json
import threading
import time
import numpy as np
import logging
import os
from functools import wraps

# Configuration du logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)


class TechnicalIndicators:
    @staticmethod
    def rsi(prices, period=14):
        """Calcul du RSI (Relative Strength Index)"""
        if len(prices) < period + 1:
            return None
        
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])
        
        if avg_loss == 0:
            return 100
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return round(rsi, 2)
    
    @staticmethod
    def stochastic(highs, lows, closes, k_period=14):
        """Calcul du Stochastique %K"""
        if len(closes) < k_period:
            return None
        
        recent_highs = highs[-k_period:]
        recent_lows = lows[-k_period:]
        current_close = closes[-1]
        
        highest_high = max(recent_highs)
        lowest_low = min(recent_lows)
        
        if highest_high == lowest_low:
            k_percent = 50
        else:
            k_percent = ((current_close - lowest_low) / (highest_high - lowest_low)) * 100
        
        return round(k_percent, 2)
    
    @staticmethod
    def atr(highs, lows, closes, period=14):
        """Calcul de l'ATR (Average True Range)"""
        if len(closes) < 2:
            return None
        
        true_ranges = []
        for i in range(1, len(closes)):
            high_low = highs[i] - lows[i]
            high_close_prev = abs(highs[i] - closes[i-1])
            low_close_prev = abs(lows[i] - closes[i-1])
            true_range = max(high_low, high_close_prev, low_close_prev)
            true_ranges.append(true_range)
        
        if len(true_ranges) < period:
            atr_value = np.mean(true_ranges)
        else:
            atr_value = np.mean(true_ranges[-period:])
        
        return round(atr_value, 2)
    
    @staticmethod
    def bollinger_bands(closes, period=20, std_dev=2):
        """Calcul des Bandes de Bollinger"""
        if len(closes) < period:
            return None, None, None
        
        recent_closes = closes[-period:]
        middle = np.mean(recent_closes)
        std = np.std(recent_closes)
        
        upper = middle + (std_dev * std)
        lower = middle - (std_dev * std)
        
        return round(upper, 2), round(middle, 2), round(lower, 2)
    
    @staticmethod
    def ema(prices, period=20):
        """Calcul de l'EMA (Exponential Moving Average)"""
        if len(prices) < period:
            return None
        
        multiplier = 2 / (period + 1)
        ema = np.mean(prices[:period])
        
        for price in prices[period:]:
            ema = (price * multiplier) + (ema * (1 - multiplier))
        
        return round(ema, 2)


class DerivDataCollector:
    def __init__(self, api_token):
        self.api_token = api_token
        self.result = {}
        self.completed = False
        self.error_occurred = False
        self.ws = None
        self.lock = threading.Lock()
        
        # Flags de réception avec lock pour thread-safety
        self._candles_5min_received = False
        self._candles_30min_received = False
        self._positions_received = False
        self._transactions_received = False
        self._auth_received = False
        
        # Stockage des données - 5 minutes
        self.highs_5min = []
        self.lows_5min = []
        self.closes_5min = []
        self.candles_5min = []
        
        # Stockage des données - 30 minutes
        self.highs_30min = []
        self.lows_30min = []
        self.closes_30min = []
        self.candles_30min = []
        
        # Autres données
        self.transactions = []
        self.v75_positions = []
        self.pending_position_details = 0
        
        # Compteurs pour retry
        self.retry_count = 0
        self.max_retries = 3
        
    def on_message(self, ws, message):
        """Traite les messages reçus de l'API"""
        try:
            data = json.loads(message)
            msg_type = list(data.keys())[0] if data else 'unknown'
            logger.info(f"📨 Message reçu: {msg_type}")
            
            # Gestion des erreurs API
            if 'error' in data:
                error = data['error']
                error_code = error.get('code', 'unknown')
                error_msg = error.get('message', 'Unknown error')
                logger.error(f"❌ Erreur API [{error_code}]: {error_msg}")
                
                # Si erreur d'auth, on ne peut rien faire
                if error_code in ['InvalidToken', 'AuthorizationRequired']:
                    self.result['error'] = error_msg
                    self.error_occurred = True
                    self.completed = True
                return
            
            # Authentification réussie
            if 'authorize' in data:
                self._handle_authorize(ws, data)
                    
            # Données de bougie reçues
            elif 'candles' in data:
                self._handle_candles(data)
                
            # Positions ouvertes
            elif 'portfolio' in data:
                self._handle_portfolio(ws, data)
                    
            # Détails des positions
            elif 'proposal_open_contract' in data:
                self._handle_position_details(data)
                    
            # Historique des transactions
            elif 'statement' in data:
                self._handle_statement(data)
                
        except json.JSONDecodeError as e:
            logger.error(f"❌ Erreur JSON: {str(e)}")
        except Exception as e:
            logger.error(f"❌ Erreur traitement message: {str(e)}")
            import traceback
            traceback.print_exc()
    
    def _handle_authorize(self, ws, data):
        """Gère la réponse d'authentification"""
        if data['authorize']:
            auth_data = data['authorize']
            with self.lock:
                self.result['balance'] = auth_data.get('balance', 'N/A')
                self.result['currency'] = auth_data.get('currency', '')
                self._auth_received = True
            
            logger.info(f"✅ Authentifié - Balance: {self.result['balance']} {self.result['currency']}")
            
            # Envoyer toutes les requêtes en parallèle
            requests = [
                {"ticks_history": "R_75", "adjust_start_time": 1, "count": 40, "end": "latest", "granularity": 300, "style": "candles", "req_id": 1},
                {"ticks_history": "R_75", "adjust_start_time": 1, "count": 30, "end": "latest", "granularity": 1800, "style": "candles", "req_id": 2},
                {"portfolio": 1, "req_id": 3},
                {"statement": 1, "description": 1, "limit": 50, "req_id": 4}
            ]
            
            for req in requests:
                try:
                    ws.send(json.dumps(req))
                    time.sleep(0.1)  # Petit délai entre les requêtes
                except Exception as e:
                    logger.error(f"❌ Erreur envoi requête: {e}")
        else:
            logger.error(f"❌ Authentification échouée")
            self.error_occurred = True
            self.completed = True
    
    def _handle_candles(self, data):
        """Gère les données de bougies"""
        candles = data.get('candles', [])
        granularity = data.get('echo_req', {}).get('granularity', 0)
        
        if not candles:
            logger.warning(f"⚠️ Aucune bougie reçue pour granularité {granularity}")
            # Marquer comme reçu même si vide pour éviter de bloquer
            with self.lock:
                if granularity == 300:
                    self._candles_5min_received = True
                elif granularity == 1800:
                    self._candles_30min_received = True
            self._check_completion()
            return
        
        with self.lock:
            if granularity == 300:  # 5 minutes
                self._process_candles_5min(candles)
                self._candles_5min_received = True
                logger.info(f"📊 {len(self.candles_5min)} bougies 5min reçues")
                
            elif granularity == 1800:  # 30 minutes
                self._process_candles_30min(candles)
                self._candles_30min_received = True
                logger.info(f"📊 {len(self.candles_30min)} bougies 30min reçues")
        
        self._check_completion()
    
    def _process_candles_5min(self, candles):
        """Traite les bougies 5 minutes"""
        self.highs_5min = []
        self.lows_5min = []
        self.closes_5min = []
        self.candles_5min = []
        
        for candle in candles:
            if isinstance(candle, dict):
                self.highs_5min.append(candle['high'])
                self.lows_5min.append(candle['low'])
                self.closes_5min.append(candle['close'])
                self.candles_5min.append({
                    'epoch': candle.get('epoch'),
                    'open': candle.get('open'),
                    'high': candle.get('high'),
                    'low': candle.get('low'),
                    'close': candle.get('close')
                })
        
        if self.closes_5min:
            self.result['indicators_5min'] = {
                'rsi': TechnicalIndicators.rsi(self.closes_5min),
                'stochastic_k': TechnicalIndicators.stochastic(self.highs_5min, self.lows_5min, self.closes_5min),
                'atr': TechnicalIndicators.atr(self.highs_5min, self.lows_5min, self.closes_5min),
                'bollinger_upper': None,
                'bollinger_middle': None,
                'bollinger_lower': None,
                'ema': TechnicalIndicators.ema(self.closes_5min)
            }
            bb = TechnicalIndicators.bollinger_bands(self.closes_5min)
            self.result['indicators_5min']['bollinger_upper'] = bb[0]
            self.result['indicators_5min']['bollinger_middle'] = bb[1]
            self.result['indicators_5min']['bollinger_lower'] = bb[2]
            self.result['bougies_5min'] = self.candles_5min
    
    def _process_candles_30min(self, candles):
        """Traite les bougies 30 minutes"""
        self.highs_30min = []
        self.lows_30min = []
        self.closes_30min = []
        self.candles_30min = []
        
        for candle in candles:
            if isinstance(candle, dict):
                self.highs_30min.append(candle['high'])
                self.lows_30min.append(candle['low'])
                self.closes_30min.append(candle['close'])
                self.candles_30min.append({
                    'epoch': candle.get('epoch'),
                    'open': candle.get('open'),
                    'high': candle.get('high'),
                    'low': candle.get('low'),
                    'close': candle.get('close')
                })
        
        if self.closes_30min:
            self.result['current_price'] = self.closes_30min[-1]
            self.result['indicators_30min'] = {
                'rsi': TechnicalIndicators.rsi(self.closes_30min),
                'stochastic_k': TechnicalIndicators.stochastic(self.highs_30min, self.lows_30min, self.closes_30min),
                'atr': TechnicalIndicators.atr(self.highs_30min, self.lows_30min, self.closes_30min),
                'bollinger_upper': None,
                'bollinger_middle': None,
                'bollinger_lower': None,
                'ema': TechnicalIndicators.ema(self.closes_30min)
            }
            bb = TechnicalIndicators.bollinger_bands(self.closes_30min)
            self.result['indicators_30min']['bollinger_upper'] = bb[0]
            self.result['indicators_30min']['bollinger_middle'] = bb[1]
            self.result['indicators_30min']['bollinger_lower'] = bb[2]
            self.result['bougies_30min'] = self.candles_30min
    
    def _handle_portfolio(self, ws, data):
        """Gère les positions du portfolio"""
        portfolio = data.get('portfolio', {})
        contracts = portfolio.get('contracts', [])
        
        with self.lock:
            self.v75_positions = [p for p in contracts if p.get('symbol') == 'R_75']
            
            if not self.v75_positions:
                self.result['positions'] = []
                self._positions_received = True
                logger.info("📋 Aucune position V75 ouverte")
            else:
                self.pending_position_details = len(self.v75_positions)
                self.result['positions'] = []
                logger.info(f"📋 {len(self.v75_positions)} positions V75 trouvées")
                
                # Demander les détails pour chaque position
                for pos in self.v75_positions:
                    contract_id = pos.get('contract_id')
                    if contract_id:
                        detail_message = {"proposal_open_contract": 1, "contract_id": contract_id}
                        try:
                            ws.send(json.dumps(detail_message))
                            time.sleep(0.05)
                        except Exception as e:
                            logger.error(f"❌ Erreur envoi détails position: {e}")
                            self.pending_position_details -= 1
        
        self._check_completion()
    
    def _handle_position_details(self, data):
        """Gère les détails d'une position"""
        details = data.get('proposal_open_contract', {})
        contract_id = details.get('contract_id')
        
        with self.lock:
            for pos in self.v75_positions:
                if pos.get('contract_id') == contract_id:
                    contract_type = pos.get('contract_type', 'N/A')
                    position_data = {
                        'contract_id': contract_id,
                        'contract_type_raw': contract_type,
                        'contract_type': self._translate_contract_type(contract_type),
                        'buy_price': pos.get('buy_price', 'N/A'),
                        'profit_percentage': details.get('profit_percentage', 'N/A')
                    }
                    self.result['positions'].append(position_data)
                    break
            
            self.pending_position_details -= 1
            if self.pending_position_details <= 0:
                self._positions_received = True
                logger.info(f"📋 Détails de {len(self.result['positions'])} positions reçus")
        
        self._check_completion()
    
    def _handle_statement(self, data):
        """Gère l'historique des transactions"""
        statement = data.get('statement', {})
        transactions = statement.get('transactions', [])
        
        logger.info(f"📋 {len(transactions)} transactions brutes reçues")
        
        with self.lock:
            # Regrouper par contract_id
            contracts = {}
            for tx in transactions:
                contract_id = tx.get('contract_id')
                if not contract_id:
                    continue
                
                if contract_id not in contracts:
                    contracts[contract_id] = {'buy': None, 'sell': None}
                
                action_type = tx.get('action_type', '')
                if action_type == 'buy':
                    contracts[contract_id]['buy'] = tx
                elif action_type == 'sell':
                    contracts[contract_id]['sell'] = tx
            
            # Construire les transactions finales
            self.transactions = []
            for contract_id, data in contracts.items():
                buy_tx = data['buy']
                sell_tx = data['sell']
                
                if buy_tx and sell_tx:
                    position = abs(buy_tx.get('amount', 0))
                    payout = sell_tx.get('amount', 0)
                    profit = round(payout - position, 2)
                    
                    status = 'won' if profit > 0 else ('lost' if profit < 0 else 'neutral')
                    
                    self.transactions.append({
                        'référence': str(sell_tx.get('transaction_id', '')),
                        'contract_id': str(contract_id),
                        'position': round(position, 2),
                        'payout': round(payout, 2),
                        'profit': profit,
                        'status': status,
                        'timestamp': sell_tx.get('timestamp', sell_tx.get('transaction_time', 0))
                    })
            
            self.transactions.sort(key=lambda x: x['timestamp'], reverse=True)
            self.result['transactions'] = self.transactions[:10]
            self._transactions_received = True
            
            logger.info(f"📋 {len(self.result['transactions'])} transactions finales")
        
        self._check_completion()
    
    def _translate_contract_type(self, contract_type):
        """Traduit les types de contrats"""
        translations = {
            'MULTUP': '📈 MULT UP (Achat/Hausse)',
            'MULTDOWN': '📉 MULT DOWN (Vente/Baisse)',
            'CALL': '📈 CALL (Achat/Hausse)',
            'PUT': '📉 PUT (Vente/Baisse)',
        }
        return translations.get(contract_type, f'📋 {contract_type}')
    
    def _check_completion(self):
        """Vérifie si toutes les données sont reçues"""
        with self.lock:
            status = f"5min={self._candles_5min_received}, 30min={self._candles_30min_received}, pos={self._positions_received}, tx={self._transactions_received}"
            logger.info(f"🔄 Status: {status}")
            
            # On attend au minimum les bougies et les positions
            if self._candles_5min_received and self._candles_30min_received and self._positions_received:
                # Transactions sont optionnelles, on attend 1s de plus si pas encore reçues
                if not self._transactions_received:
                    logger.info("⏳ En attente des transactions (optionnel)...")
                    return
                
                logger.info("✅ Toutes les données reçues!")
                self.completed = True
    
    def on_error(self, ws, error):
        logger.error(f"❌ WebSocket Error: {str(error)}")
        with self.lock:
            self.error_occurred = True
            self.result['error'] = str(error)
    
    def on_close(self, ws, close_status_code, close_msg):
        logger.info(f"🔌 WebSocket fermé: {close_status_code}")
    
    def on_open(self, ws):
        logger.info("🔌 WebSocket ouvert, authentification...")
        try:
            ws.send(json.dumps({"authorize": self.api_token}))
        except Exception as e:
            logger.error(f"❌ Erreur envoi auth: {e}")
            self.error_occurred = True
    
    def collect_data(self, timeout=60):
        """Lance la collecte de données avec retry"""
        for attempt in range(self.max_retries):
            logger.info(f"🚀 Tentative {attempt + 1}/{self.max_retries}")
            
            # Reset pour nouvelle tentative
            self._reset_state()
            
            websocket.enableTrace(False)
            self.ws = websocket.WebSocketApp(
                "wss://ws.derivws.com/websockets/v3?app_id=1089",
                on_message=self.on_message,
                on_error=self.on_error,
                on_close=self.on_close,
                on_open=self.on_open
            )
            
            # Lancer dans un thread
            thread = threading.Thread(target=self.ws.run_forever, kwargs={'ping_interval': 30, 'ping_timeout': 10})
            thread.daemon = True
            thread.start()
            
            # Attendre la complétion
            start_time = time.time()
            while not self.completed and not self.error_occurred and (time.time() - start_time) < timeout:
                time.sleep(0.5)
                
                # Forcer la complétion si données essentielles reçues après délai
                elapsed = time.time() - start_time
                if elapsed > 15:  # Après 15s, vérifier si on a assez
                    with self.lock:
                        if self._candles_5min_received and self._candles_30min_received and self._positions_received:
                            if not self._transactions_received:
                                logger.info("⚠️ Timeout transactions, on continue sans")
                                self.result['transactions'] = self.result.get('transactions', [])
                            self.completed = True
            
            # Fermer proprement
            try:
                if self.ws:
                    self.ws.close()
            except:
                pass
            
            # Vérifier le résultat
            if self.completed and not self.error_occurred:
                if self._validate_result():
                    logger.info("✅ Collecte réussie!")
                    return self.result
                else:
                    logger.warning(f"⚠️ Données incomplètes, retry...")
            elif self.error_occurred:
                logger.warning(f"⚠️ Erreur lors de la tentative {attempt + 1}")
            else:
                logger.warning(f"⚠️ Timeout lors de la tentative {attempt + 1}")
            
            # Attendre avant retry
            if attempt < self.max_retries - 1:
                time.sleep(2)
        
        # Échec après tous les retries
        if 'error' not in self.result:
            self.result['error'] = 'Échec après plusieurs tentatives'
        
        return self.result
    
    def _reset_state(self):
        """Réinitialise l'état pour une nouvelle tentative"""
        with self.lock:
            self.completed = False
            self.error_occurred = False
            self._candles_5min_received = False
            self._candles_30min_received = False
            self._positions_received = False
            self._transactions_received = False
            self._auth_received = False
            self.highs_5min = []
            self.lows_5min = []
            self.closes_5min = []
            self.candles_5min = []
            self.highs_30min = []
            self.lows_30min = []
            self.closes_30min = []
            self.candles_30min = []
            self.transactions = []
            self.v75_positions = []
            self.pending_position_details = 0
            # Garder balance/currency si déjà reçus
            if 'error' in self.result:
                del self.result['error']
    
    def _validate_result(self):
        """Valide que le résultat contient les données essentielles"""
        required = ['bougies_5min', 'bougies_30min', 'indicators_5min', 'indicators_30min']
        for key in required:
            if key not in self.result:
                return False
            if key.startswith('bougies_') and not self.result[key]:
                return False
            if key.startswith('indicators_') and not self.result[key]:
                return False
        return True


def get_v75_data():
    """Fonction pour récupérer les données V75"""
    API_TOKEN = "DvXO5nDy5KFG3vW"
    
    try:
        collector = DerivDataCollector(API_TOKEN)
        data = collector.collect_data(timeout=60)
        return data
    except Exception as e:
        logger.error(f"❌ Erreur get_v75_data: {str(e)}")
        return {'error': str(e)}


@app.route('/')
def v75_data():
    """API endpoint pour récupérer les données V75"""
    try:
        logger.info("📊 Récupération des données V75...")
        
        data = get_v75_data()
        
        if 'error' in data:
            logger.error(f"❌ Erreur: {data['error']}")
            return jsonify({
                'success': False,
                'error': data['error']
            }), 500
        
        response = {
            'success': True,
            'timestamp': time.time(),
            'account': {
                'balance': data.get('balance', 'N/A'),
                'currency': data.get('currency', '')
            },
            'market': {
                'symbol': 'V75',
                'current_price': data.get('current_price', 'N/A')
            },
            'bougies_5min': data.get('bougies_5min', []),
            'indicators_5min': data.get('indicators_5min', {}),
            'bougies_30min': data.get('bougies_30min', []),
            'indicators_30min': data.get('indicators_30min', {}),
            'positions': data.get('positions', []),
            'transactions': data.get('transactions', [])
        }
        
        logger.info(f"✅ Données récupérées - {len(response.get('bougies_5min', []))} bougies 5min, {len(response.get('bougies_30min', []))} bougies 30min")
        return jsonify(response)
        
    except Exception as e:
        logger.error(f"❌ Erreur générale: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/health')
def health():
    """Vérifier que le service fonctionne"""
    return jsonify({
        'status': 'active',
        'service': 'V75 Deriv API - Robust Edition',
        'timestamp': time.time()
    })


@app.route('/open_position', methods=['POST'])
def open_position():
    """Endpoint pour ouvrir une position"""
    try:
        data = request.get_json()
        logger.info(f"📥 Open position request: {data}")
        
        if not data:
            return jsonify({'success': False, 'error': 'Aucune donnée JSON reçue'}), 400
        
        if isinstance(data, list):
            if len(data) == 0:
                return jsonify({'success': False, 'error': 'Tableau vide reçu'}), 400
            data = data[0]
        
        if 'buy' not in data or 'parameters' not in data:
            return jsonify({'success': False, 'error': 'Champs "buy" ou "parameters" manquants'}), 400
        
        buy = data['buy']
        parameters = data['parameters']
        
        required_params = ['contract_type', 'symbol', 'amount', 'multiplier', 'stop_loss', 'take_profit']
        for param in required_params:
            if param not in parameters:
                return jsonify({'success': False, 'error': f'Paramètre "{param}" manquant'}), 400
        
        API_TOKEN = "DvXO5nDy5KFG3vW"
        
        def send_buy_order():
            ws = None
            try:
                ws = websocket.WebSocket()
                ws.settimeout(30)
                ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089")
                
                ws.send(json.dumps({"authorize": API_TOKEN}))
                auth_response = json.loads(ws.recv())
                
                if 'error' in auth_response:
                    raise Exception(f"Auth failed: {auth_response['error'].get('message', 'Unknown')}")
                
                buy_message = {
                    "buy": buy,
                    "price": float(parameters['amount']),
                    "parameters": {
                        "contract_type": parameters['contract_type'],
                        "symbol": parameters['symbol'],
                        "amount": float(parameters['amount']),
                        "basis": "stake",
                        "multiplier": int(parameters['multiplier']),
                        "currency": "USD",
                        "limit_order": {
                            "stop_loss": float(parameters['stop_loss']),
                            "take_profit": float(parameters['take_profit'])
                        }
                    }
                }
                
                logger.info(f"📤 Sending buy order: {json.dumps(buy_message, indent=2)}")
                ws.send(json.dumps(buy_message))
                
                response = ws.recv()
                return response
                
            except Exception as e:
                logger.error(f"❌ WebSocket error: {str(e)}")
                raise e
            finally:
                if ws:
                    try:
                        ws.close()
                    except:
                        pass
        
        result = send_buy_order()
        result_json = json.loads(result)
        
        return jsonify({
            'success': True,
            'response': result_json,
            'sent_data': {'buy': buy, 'parameters': parameters}
        })
        
    except Exception as e:
        logger.error(f"❌ Open position error: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/close_position', methods=['POST'])
def close_position():
    """Endpoint pour fermer une position"""
    try:
        data = request.get_json()
        
        if not data or not isinstance(data, list):
            return jsonify({'success': False, 'error': 'Format invalide'}), 400
        
        if not all('sell' in d and 'contract_id' in d for d in data):
            return jsonify({'success': False, 'error': 'Champs sell/contract_id manquants'}), 400
        
        API_TOKEN = "DvXO5nDy5KFG3vW"
        contract_id = data[0]['contract_id']
        sell_price = data[0]['sell']
        
        def send_sell_order():
            ws = None
            try:
                ws = websocket.WebSocket()
                ws.settimeout(30)
                ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089")
                
                ws.send(json.dumps({"authorize": API_TOKEN}))
                auth_response = json.loads(ws.recv())
                
                if 'error' in auth_response:
                    raise Exception(f"Auth failed: {auth_response['error'].get('message', 'Unknown')}")
                
                sell_message = {"sell": contract_id, "price": sell_price}
                ws.send(json.dumps(sell_message))
                
                response = ws.recv()
                return response
                
            except Exception as e:
                raise e
            finally:
                if ws:
                    try:
                        ws.close()
                    except:
                        pass
        
        result = send_sell_order()
        return jsonify({'success': True, 'response': json.loads(result)})
        
    except Exception as e:
        logger.error(f"❌ Close position error: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    PORT = int(os.environ.get('PORT', 5000))
    
    print("🚀 API Deriv V75 - Robust Edition")
    print(f"📍 Port: {PORT}")
    print("✨ Features: Auto-retry, Thread-safe, Better error handling")
    
    app.run(host='0.0.0.0', port=PORT, debug=False)