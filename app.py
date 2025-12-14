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
        
        # Stockage des données
        self.highs = []
        self.lows = []
        self.closes = []
        self.candles = []
        self.transactions = []
        self.v75_positions = []
        self.positions_detailed = False
        self.candles_received = False
        self.transactions_received = False
        
    def on_message(self, ws, message):
        """Traite les messages reçus de l'API"""
        try:
            data = json.loads(message)
            
            # Authentification réussie
            if 'authorize' in data and data['authorize']:
                auth_data = data['authorize']
                self.result['balance'] = auth_data.get('balance', 'N/A')
                self.result['currency'] = auth_data.get('currency', '')
                
                # Récupérer les bougies V75
                candles_message = {
                    "ticks_history": "R_75",
                    "adjust_start_time": 1,
                    "count": 50,
                    "end": "latest",
                    "granularity": 300,  # 5 minutes
                    "style": "candles"
                }
                ws.send(json.dumps(candles_message))
                
                # Récupérer les positions
                portfolio_message = {"portfolio": 1}
                ws.send(json.dumps(portfolio_message))
                
                # Récupérer les 15 dernières transactions
                statement_message = {"statement": 1, "limit": 15}
                ws.send(json.dumps(statement_message))
                
            # Données de bougie reçues
            elif 'candles' in data:
                candles = data['candles']
                parsed_candles = []
                
                # Extraire les données (format dictionnaire)
                for candle in candles:
                    if isinstance(candle, dict):
                        self.highs.append(candle['high'])
                        self.lows.append(candle['low'])
                        self.closes.append(candle['close'])
                        parsed_candles.append({
                            'epoch': candle.get('epoch'),
                            'open': candle.get('open'),
                            'high': candle.get('high'),
                            'low': candle.get('low'),
                            'close': candle.get('close')
                        })
                # Conserver les 50 dernières bougies reçues (ordre API)
                self.candles = parsed_candles
                
                # Calculer les indicateurs
                if len(self.closes) > 0:
                    current_price = self.closes[-1]
                    self.result['current_price'] = current_price
                    
                    # Calcul des indicateurs
                    rsi = TechnicalIndicators.rsi(self.closes)
                    stoch_k = TechnicalIndicators.stochastic(self.highs, self.lows, self.closes)
                    atr = TechnicalIndicators.atr(self.highs, self.lows, self.closes)
                    bb_upper, bb_middle, bb_lower = TechnicalIndicators.bollinger_bands(self.closes)
                    ema = TechnicalIndicators.ema(self.closes)
                    
                    self.result['indicators'] = {
                        'rsi': rsi,
                        'stochastic_k': stoch_k,
                        'atr': atr,
                        'bollinger_upper': bb_upper,
                        'bollinger_middle': bb_middle,
                        'bollinger_lower': bb_lower,
                        'ema': ema
                    }
                    self.result['candles'] = self.candles
                    
                self.candles_received = True
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
                    
            # Historique des transactions
            elif 'statement' in data:
                statement = data['statement']
                if 'transactions' in statement:
                    transactions = statement['transactions']
                    
                    # Fusionner les transactions par contract_id (chaque transaction a 2 lignes: buy et sell)
                    tx_map = {}
                    
                    for transaction in transactions:
                        contract_id = transaction.get('identifiant_du_contrat', transaction.get('contract_id'))
                        
                        if contract_id not in tx_map:
                            tx_map[contract_id] = {
                                'montant_misé': 0,
                                'paiement': 0,
                                'référence': None,
                                'app_id': transaction.get('app_id')
                            }
                        
                        # Accumuler montant et paiement
                        montant = transaction.get('montant', 0)
                        paiement = transaction.get('paiement', 0)
                        
                        if montant < 0:
                            tx_map[contract_id]['montant_misé'] = abs(montant)
                        if paiement > 0:
                            tx_map[contract_id]['paiement'] = paiement
                        
                        # Garder la référence (première valeur non-null trouvée)
                        ref = transaction.get('référence')
                        if ref and ref != 'null' and ref != 'nulle' and ref != 'N/A':
                            tx_map[contract_id]['référence'] = ref
                    
                    # Convertir en liste et calculer profit/status
                    for contract_id, tx_data in tx_map.items():
                        montant_misé = tx_data['montant_misé']
                        paiement = tx_data['paiement']
                        profit = paiement - montant_misé
                        
                        # Déterminer le statut
                        if profit > 0:
                            status = 'won'
                        elif profit < 0:
                            status = 'lost'
                        else:
                            status = 'neutral'
                        
                        cleaned_tx = {
                            'référence': tx_data['référence'],
                            'contract_id': contract_id,
                            'position': montant_misé,
                            'payout': paiement,
                            'profit': round(profit, 2),
                            'status': status
                        }
                        self.transactions.append(cleaned_tx)
                    
                    # Limiter à 15 transactions max et garder les plus récentes
                    self.result['transactions'] = self.transactions[-15:] if len(self.transactions) > 15 else self.transactions
                    self.transactions_received = True
                    self.check_completion()
                
        except Exception as e:
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
        """Vérifie si toutes les données sont reçues"""
        if self.candles_received and self.positions_detailed and self.transactions_received:
            self.completed = True
    
    def on_error(self, ws, error):
        self.result['error'] = str(error)
        self.completed = True
    
    def on_close(self, ws, close_status_code, close_msg):
        pass
    
    def on_open(self, ws):
        # S'authentifier
        auth_message = {"authorize": self.api_token}
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
        
        # Attendre que la collecte soit terminée
        timeout = 15
        start_time = time.time()
        
        while not self.completed and (time.time() - start_time) < timeout:
            time.sleep(0.5)
        
        ws.close()
        
        if not self.completed:
            self.result['error'] = 'Timeout - données incomplètes'
        
        return self.result

def get_v75_data():
    """Fonction pour récupérer les données V75"""
    API_TOKEN = "0BV3Ve4oK74HMlU"
    
    try:
        collector = DerivDataCollector(API_TOKEN)
        data = collector.collect_data()
        return data
    except Exception as e:
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
            'indicators': data.get('indicators', {}),
            'positions': data.get('positions', []),
            'candles': data.get('candles', []),
            'transactions': data.get('transactions', [])
        }
        
        logger.info("✅ Données récupérées avec succès")
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
        
        API_TOKEN = "0BV3Ve4oK74HMlU"
        
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

        API_TOKEN = "0BV3Ve4oK74HMlU"
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
