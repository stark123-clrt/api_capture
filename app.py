from flask import Flask, jsonify, request
import asyncio
import websocket
import json
import threading
import time
import numpy as np
import logging

# Configuration du logging
logging.basicConfig(level=logging.INFO)
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
        
        # Multiplicateur de lissage
        multiplier = 2 / (period + 1)
        
        # EMA simple au départ = SMA
        ema = np.mean(prices[:period])
        
        # Appliquer la formule EMA sur le reste des données
        for price in prices[period:]:
            ema = (price * multiplier) + (ema * (1 - multiplier))
        
        return round(ema, 2)

class DerivDataCollector:
    def __init__(self, api_token):
        self.api_token = api_token
        self.result = {}
        self.completed = False
        self.last_message_time = time.time()  # Pour attendre 2s après dernier message
        
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
        self.positions_detailed = False
        self.candles_5min_received = False
        self.candles_30min_received = False
        self.transactions_received = False
        
    def on_message(self, ws, message):
        """Traite les messages reçus de l'API"""
        try:
            data = json.loads(message)
            self.last_message_time = time.time()  # Mettre à jour le timestamp
            logger.info(f"📨 Message reçu: {list(data.keys())}")
            
            # Authentification réussie
            if 'authorize' in data:
                if data['authorize']:
                    auth_data = data['authorize']
                    self.result['balance'] = auth_data.get('balance', 'N/A')
                    self.result['currency'] = auth_data.get('currency', '')
                    logger.info(f"✅ Authentifié - Balance: {self.result['balance']} {self.result['currency']}")
                    
                    # Récupérer les bougies 5 minutes (40 bougies)
                    candles_5min_message = {
                        "ticks_history": "R_75",
                        "adjust_start_time": 1,
                        "count": 40,
                        "end": "latest",
                        "granularity": 300,  # 5 minutes
                        "style": "candles"
                    }
                    ws.send(json.dumps(candles_5min_message))
                    
                    # Récupérer les bougies 30 minutes (30 bougies)
                    candles_30min_message = {
                        "ticks_history": "R_75",
                        "adjust_start_time": 1,
                        "count": 30,
                        "end": "latest",
                        "granularity": 1800,  # 30 minutes
                        "style": "candles"
                    }
                    ws.send(json.dumps(candles_30min_message))
                    
                    # Récupérer les positions
                    portfolio_message = {"portfolio": 1}
                    ws.send(json.dumps(portfolio_message))
                    
                    # Récupérer les 50 dernières transactions
                    statement_message = {"statement": 1, "description": 1, "limit": 50}
                    ws.send(json.dumps(statement_message))
                else:
                    logger.error(f"❌ Authentification échouée: {data.get('authorize')}")
                    
            # Données de bougie reçues
            elif 'candles' in data:
                candles = data['candles']
                
                # Déterminer le timeframe en regardant la granularité dans echo_req
                granularity = data.get('echo_req', {}).get('granularity', 0)
                
                if granularity == 300:  # 5 minutes
                    parsed_candles_5min = []
                    for candle in candles:
                        if isinstance(candle, dict):
                            self.highs_5min.append(candle['high'])
                            self.lows_5min.append(candle['low'])
                            self.closes_5min.append(candle['close'])
                            parsed_candles_5min.append({
                                'epoch': candle.get('epoch'),
                                'open': candle.get('open'),
                                'high': candle.get('high'),
                                'low': candle.get('low'),
                                'close': candle.get('close')
                            })
                    self.candles_5min = parsed_candles_5min
                    
                    # Calculer les indicateurs 5 minutes
                    if len(self.closes_5min) > 0:
                        rsi_5min = TechnicalIndicators.rsi(self.closes_5min)
                        stoch_k_5min = TechnicalIndicators.stochastic(self.highs_5min, self.lows_5min, self.closes_5min)
                        atr_5min = TechnicalIndicators.atr(self.highs_5min, self.lows_5min, self.closes_5min)
                        bb_upper_5min, bb_middle_5min, bb_lower_5min = TechnicalIndicators.bollinger_bands(self.closes_5min)
                        ema_5min = TechnicalIndicators.ema(self.closes_5min)
                        
                        self.result['indicators_5min'] = {
                            'rsi': rsi_5min,
                            'stochastic_k': stoch_k_5min,
                            'atr': atr_5min,
                            'bollinger_upper': bb_upper_5min,
                            'bollinger_middle': bb_middle_5min,
                            'bollinger_lower': bb_lower_5min,
                            'ema': ema_5min
                        }
                        self.result['bougies_5min'] = self.candles_5min
                    
                    logger.info(f"📊 {len(self.candles_5min)} bougies 5min reçues")
                    self.candles_5min_received = True
                    
                elif granularity == 1800:  # 30 minutes
                    parsed_candles_30min = []
                    for candle in candles:
                        if isinstance(candle, dict):
                            self.highs_30min.append(candle['high'])
                            self.lows_30min.append(candle['low'])
                            self.closes_30min.append(candle['close'])
                            parsed_candles_30min.append({
                                'epoch': candle.get('epoch'),
                                'open': candle.get('open'),
                                'high': candle.get('high'),
                                'low': candle.get('low'),
                                'close': candle.get('close')
                            })
                    self.candles_30min = parsed_candles_30min
                    
                    # Calculer les indicateurs 30 minutes
                    if len(self.closes_30min) > 0:
                        current_price = self.closes_30min[-1]
                        self.result['current_price'] = current_price
                        
                        rsi_30min = TechnicalIndicators.rsi(self.closes_30min)
                        stoch_k_30min = TechnicalIndicators.stochastic(self.highs_30min, self.lows_30min, self.closes_30min)
                        atr_30min = TechnicalIndicators.atr(self.highs_30min, self.lows_30min, self.closes_30min)
                        bb_upper_30min, bb_middle_30min, bb_lower_30min = TechnicalIndicators.bollinger_bands(self.closes_30min)
                        ema_30min = TechnicalIndicators.ema(self.closes_30min)
                        
                        self.result['indicators_30min'] = {
                            'rsi': rsi_30min,
                            'stochastic_k': stoch_k_30min,
                            'atr': atr_30min,
                            'bollinger_upper': bb_upper_30min,
                            'bollinger_middle': bb_middle_30min,
                            'bollinger_lower': bb_lower_30min,
                            'ema': ema_30min
                        }
                        self.result['bougies_30min'] = self.candles_30min
                    
                    logger.info(f"📊 {len(self.candles_30min)} bougies 30min reçues")
                    self.candles_30min_received = True
                
                self.check_completion()
                
            # Positions ouvertes
            elif 'portfolio' in data:
                portfolio = data['portfolio']
                if 'contracts' in portfolio:
                    positions = portfolio['contracts']
                    self.v75_positions = [p for p in positions if p.get('symbol') == 'R_75']
                    
                    if self.v75_positions:
                        # Demander les détails pour chaque position
                        for pos in self.v75_positions:
                            contract_id = pos.get('contract_id')
                            if contract_id:
                                detail_message = {
                                    "proposal_open_contract": 1,
                                    "contract_id": contract_id
                                }
                                ws.send(json.dumps(detail_message))
                    else:
                        self.result['positions'] = []
                        self.positions_detailed = True
                        self.check_completion()
                else:
                    self.result['positions'] = []
                    self.positions_detailed = True
                    self.check_completion()
                    
            # Détails des positions (avec pourcentage exact)
            elif 'proposal_open_contract' in data:
                details = data['proposal_open_contract']
                contract_id = details.get('contract_id')
                
                # Initialiser positions si pas encore fait
                if 'positions' not in self.result:
                    self.result['positions'] = []
                
                # Trouver la position correspondante
                for pos in self.v75_positions:
                    if pos.get('contract_id') == contract_id:
                        contract_type = pos.get('contract_type', 'N/A')
                        
                        # Traduire le type de contrat
                        contract_display = self.translate_contract_type(contract_type)
                        
                        position_data = {
                            'contract_id': contract_id,
                            'contract_type_raw': contract_type,
                            'contract_type': contract_display,
                            'buy_price': pos.get('buy_price', 'N/A'),
                            'profit_percentage': details.get('profit_percentage', 'N/A')
                        }
                        self.result['positions'].append(position_data)
                        break
                
                self.positions_detailed = True
                self.check_completion()
                    
            # Historique des transactions - CORRIGÉ
            elif 'statement' in data:
                statement = data['statement']
                logger.info(f"📋 Statement reçu")
                
                if 'transactions' in statement:
                    transactions = statement['transactions']
                    logger.info(f"📋 {len(transactions)} transactions brutes reçues")
                    
                    # On regroupe les transactions par contract_id pour avoir buy + sell ensemble
                    contracts = {}
                    
                    for transaction in transactions:
                        action_type = transaction.get('action_type', '')
                        contract_id = transaction.get('contract_id')
                        
                        if not contract_id:
                            continue
                        
                        if contract_id not in contracts:
                            contracts[contract_id] = {'buy': None, 'sell': None}
                        
                        if action_type == 'buy':
                            contracts[contract_id]['buy'] = transaction
                        elif action_type == 'sell':
                            contracts[contract_id]['sell'] = transaction
                    
                    # Construire les transactions finales
                    for contract_id, data in contracts.items():
                        buy_tx = data['buy']
                        sell_tx = data['sell']
                        
                        # On ne garde que les contrats terminés (avec buy ET sell)
                        if buy_tx and sell_tx:
                            position = abs(buy_tx.get('amount', 0))
                            payout = sell_tx.get('amount', 0)
                            profit = round(payout - position, 2)
                            
                            # Déterminer le statut
                            if profit > 0:
                                status = 'won'
                            elif profit < 0:
                                status = 'lost'
                            else:
                                status = 'neutral'
                            
                            cleaned_tx = {
                                'référence': str(sell_tx.get('transaction_id', '')),
                                'contract_id': str(contract_id),
                                'position': round(position, 2),
                                'payout': round(payout, 2),
                                'profit': profit,
                                'status': status,
                                'timestamp': sell_tx.get('timestamp', sell_tx.get('transaction_time', 0))
                            }
                            self.transactions.append(cleaned_tx)
                    
                    # Trier par timestamp décroissant et garder les 10 plus récentes
                    self.transactions.sort(key=lambda x: x['timestamp'], reverse=True)
                    self.result['transactions'] = self.transactions[:10]
                    logger.info(f"📋 {len(self.result['transactions'])} transactions finales")
                else:
                    logger.warning("⚠️ Pas de clé 'transactions' dans le statement")
                    self.result['transactions'] = []
                
                # Marquer comme reçu
                self.transactions_received = True
                self.check_completion()
            
            # Gestion des erreurs API
            elif 'error' in data:
                error = data['error']
                logger.error(f"❌ Erreur API: {error.get('message', 'Unknown error')}")
                
        except Exception as e:
            logger.error(f"❌ Erreur traitement message: {str(e)}")
            import traceback
            traceback.print_exc()
            self.result['error'] = str(e)
            self.completed = True
    
    def translate_contract_type(self, contract_type):
        """Traduit les types de contrats Deriv en français"""
        translations = {
            'MULTUP': '📈 MULT UP (Achat/Hausse)',
            'MULTDOWN': '📉 MULT DOWN (Vente/Baisse)',
            'CALL': '📈 CALL (Achat/Hausse)',
            'PUT': '📉 PUT (Vente/Baisse)',
            'CALLE': '📈 CALL EUROPÉEN (Achat)',
            'PUTE': '📉 PUT EUROPÉEN (Vente)',
            'ONETOUCH': '🎯 ONE TOUCH',
            'NOTOUCH': '🚫 NO TOUCH',
            'RANGE': '📊 RANGE',
            'UPORDOWN': '⚡ UP OR DOWN',
            'EXPIRYMISS': '❌ EXPIRY MISS',
            'EXPIRYRANGE': '🎯 EXPIRY RANGE',
            'DIGITMATCH': '🔢 DIGIT MATCH',
            'DIGITDIFF': '🔢 DIGIT DIFFER'
        }
        
        return translations.get(contract_type, f'📋 {contract_type}')
    
    def check_completion(self):
        """Vérifie si toutes les données essentielles sont reçues (transactions optionnelles)"""
        logger.info(f"🔄 Check: candles_5min={self.candles_5min_received}, candles_30min={self.candles_30min_received}, positions={self.positions_detailed}, transactions={self.transactions_received}")
        # Les transactions sont optionnelles - on attend les deux timeframes de bougies et les positions
        if self.candles_5min_received and self.candles_30min_received and self.positions_detailed:
            # Attendre 2 secondes après le dernier message pour être sûr que tout est arrivé
            if (time.time() - self.last_message_time) >= 2.0:
                logger.info("✅ Données essentielles reçues (après délai de 2s)!")
                self.completed = True
            else:
                logger.info(f"⏳ En attente de 2s après dernier message (reste {2.0 - (time.time() - self.last_message_time):.1f}s)")
    
    def on_error(self, ws, error):
        logger.error(f"❌ WebSocket Error: {str(error)}")
        self.result['error'] = str(error)
        self.completed = True
    
    def on_close(self, ws, close_status_code, close_msg):
        logger.info(f"🔌 WebSocket fermé: {close_status_code} - {close_msg}")
    
    def on_open(self, ws):
        logger.info("🔌 WebSocket ouvert, envoi de l'authentification...")
        # S'authentifier
        auth_message = {"authorize": self.api_token}
        logger.info(f"🔐 Token utilisé: {self.api_token}")
        ws.send(json.dumps(auth_message))
    
    def collect_data(self):
        """Lance la collecte de données"""
        websocket.enableTrace(False)
        ws = websocket.WebSocketApp(
            "wss://ws.derivws.com/websockets/v3?app_id=1089",
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
            on_open=self.on_open
        )
        
        # Lancer dans un thread
        thread = threading.Thread(target=ws.run_forever)
        thread.daemon = True
        thread.start()
        
        # Attendre que la collecte soit terminée (compte réel = plus lent, 2 timeframes)
        timeout = 90
        start_time = time.time()
        
        while not self.completed and (time.time() - start_time) < timeout:
            time.sleep(0.5)
            # Re-vérifier completion régulièrement pour le délai de 2s
            if self.candles_5min_received and self.candles_30min_received and self.positions_detailed:
                self.check_completion()
        
        ws.close()
        
        return self.result

def get_v75_data():
    """Fonction pour récupérer les données V75"""
    API_TOKEN = "DvXO5nDy5KFG3vW"
    
    try:
        collector = DerivDataCollector(API_TOKEN)
        data = collector.collect_data()
        return data
    except Exception as e:
        logger.error(f"❌ Erreur get_v75_data: {str(e)}")
        return {'error': str(e)}

@app.route('/')
def v75_data():
    """
    API endpoint pour récupérer les données V75
    """
    try:
        logger.info("📊 Récupération des données V75...")
        
        # Récupérer les données
        data = get_v75_data()
        
        if 'error' in data:
            logger.error(f"❌ Erreur: {data['error']}")
            return jsonify({
                'success': False,
                'error': data['error']
            }), 500
        
        # Formater la réponse
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
        
        logger.info(f"✅ Données récupérées - {len(response['transactions'])} transactions")
        return jsonify(response)
        
    except Exception as e:
        logger.error(f"❌ Erreur générale: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/health')
def health():
    """Vérifier que le service fonctionne"""
    return jsonify({
        'status': 'active',
        'service': 'V75 Deriv API',
        'timestamp': time.time()
    })


@app.route('/open_position', methods=['POST'])
def open_position():
    """
    Endpoint pour ouvrir une position d'achat (buy).
    Requiert un JSON avec les champs 'buy' et 'parameters' :
    - 'buy' : 1 pour acheter,
    - 'parameters' : dictionnaire avec 'contract_type', 'symbol', 'amount' (montant à miser), 'multiplier', 'stop_loss', 'take_profit'.
    """
    try:
        # Récupération des données
        data = request.get_json()
        print(f"Données reçues: {data}")  # Debug
        
        # Validation du format de base
        if not data:
            return jsonify({'success': False, 'error': 'Aucune donnée JSON reçue'}), 400
            
        # Si les données sont un tableau, prendre le premier élément
        if isinstance(data, list):
            if len(data) == 0:
                return jsonify({'success': False, 'error': 'Tableau vide reçu'}), 400
            data = data[0]
        
        # Validation des champs requis
        if 'buy' not in data:
            return jsonify({'success': False, 'error': 'Champ "buy" manquant'}), 400
            
        if 'parameters' not in data:
            return jsonify({'success': False, 'error': 'Champ "parameters" manquant'}), 400
        
        buy = data['buy']
        parameters = data['parameters']
        
        # Validation des paramètres requis
        required_params = ['contract_type', 'symbol', 'amount', 'multiplier', 'stop_loss', 'take_profit']
        for param in required_params:
            if param not in parameters:
                return jsonify({'success': False, 'error': f'Paramètre "{param}" manquant'}), 400
        
        API_TOKEN = "DvXO5nDy5KFG3vW"
        
        # Fonction pour envoyer l'ordre via WebSocket
        def send_buy_order():
            ws = None
            try:
                ws = websocket.WebSocket()
                ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089")
                
                # Autorisation
                auth_message = {"authorize": API_TOKEN}
                ws.send(json.dumps(auth_message))
                
                # Attendre la réponse d'autorisation
                auth_response = ws.recv()
                print(f"Réponse d'autorisation: {auth_response}")
                
                # Format correct pour MULTUP/MULTDOWN avec limit_order
                buy_message = {
                    "buy": buy,  # 1 = acheter, garde la valeur du signal
                    "price": float(parameters['amount']),  # Prix à payer (requis par l'API)
                    "parameters": {
                        "contract_type": parameters['contract_type'],
                        "symbol": parameters['symbol'],
                        "amount": float(parameters['amount']),
                        "basis": "stake",
                        "multiplier": int(parameters['multiplier']),
                        "currency": "USD",  # Devise du compte (requis par l'API)
                        "limit_order": {
                            "stop_loss": float(parameters['stop_loss']),
                            "take_profit": float(parameters['take_profit'])
                        }
                    }
                }
                
                print(f"Message d'achat avec limit_order: {json.dumps(buy_message, indent=2)}")
                
                # Envoyer l'ordre
                ws.send(json.dumps(buy_message))
                
                # Attendre la réponse
                response = ws.recv()
                print(f"Réponse de l'ordre: {response}")
                
                return response
                
            except Exception as e:
                print(f"Erreur WebSocket: {str(e)}")
                raise e
            finally:
                if ws:
                    ws.close()
        
        # Exécuter l'ordre
        result = send_buy_order()
        result_json = json.loads(result)
        
        return jsonify({
            'success': True, 
            'response': result_json,
            'sent_data': {
                'buy': buy,
                'parameters': parameters
            }
        })
        
    except json.JSONDecodeError as e:
        return jsonify({'success': False, 'error': f'Erreur de décodage JSON: {str(e)}'}), 400
    except KeyError as e:
        return jsonify({'success': False, 'error': f'Clé manquante: {str(e)}'}), 400
    except ValueError as e:
        return jsonify({'success': False, 'error': f'Erreur de valeur: {str(e)}'}), 400
    except Exception as e:
        print(f"Erreur générale: {str(e)}")
        return jsonify({'success': False, 'error': f'Erreur serveur: {str(e)}'}), 500



@app.route('/close_position', methods=['POST'])
def close_position():
    """
    Endpoint pour fermer une position ouverte.
    Requiert un JSON avec les champs 'sell' et 'contract_id'.
    """
    try:
        data = request.get_json()
        
        if not data or not isinstance(data, list) or not all('sell' in d and 'contract_id' in d for d in data):
            return jsonify({'success': False, 'error': 'Format de requête invalide. Attendu: [{"sell":..., "contract_id":...}]'}), 400

        API_TOKEN = "DvXO5nDy5KFG3vW"
        contract_id = data[0]['contract_id']
        sell_price = data[0]['sell']

        # Préparation de la commande WebSocket
        def send_sell_order():
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089")
            ws.send(json.dumps({"authorize": API_TOKEN}))
            time.sleep(1)  # Laisser le temps à l'auth

            sell_message = {
                "sell": contract_id,
                "price": sell_price
            }

            ws.send(json.dumps(sell_message))
            time.sleep(2)  # Attente de la confirmation
            response = ws.recv()
            ws.close()
            return response

        result = send_sell_order()
        return jsonify({'success': True, 'response': json.loads(result)})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    # Port pour Render (utilise la variable d'environnement PORT)
    import os
    PORT = int(os.environ.get('PORT', 5000))
    
    print("🚀 API Deriv V10 démarrée")
    print(f"📍 Port: {PORT}")
    
    app.run(host='0.0.0.0', port=PORT, debug=False)
