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

class DerivDataCollector:
    def __init__(self, api_token):
        self.api_token = api_token
        self.result = {}
        self.completed = False
        
        # Stockage des données
        self.highs = []
        self.lows = []
        self.closes = []
        self.vix75_positions = []
        self.positions_detailed = False
        self.candles_received = False
        
    def on_message(self, ws, message):
        """Traite les messages reçus de l'API"""
        try:
            data = json.loads(message)
            
            # Authentification réussie
            if 'authorize' in data and data['authorize']:
                auth_data = data['authorize']
                self.result['balance'] = auth_data.get('balance', 'N/A')
                self.result['currency'] = auth_data.get('currency', '')
                
                # Récupérer les bougies
                candles_message = {
                    "ticks_history": "R_75",
                    "adjust_start_time": 1,
                    "count": 50,
                    "end": "latest",
                    "granularity": 900,  # 15 minutes
                    "style": "candles"
                }
                ws.send(json.dumps(candles_message))
                
                # Récupérer les positions
                portfolio_message = {"portfolio": 1}
                ws.send(json.dumps(portfolio_message))
                
            # Données de bougie reçues
            elif 'candles' in data:
                candles = data['candles']
                
                # Extraire les données (format dictionnaire)
                for candle in candles:
                    if isinstance(candle, dict):
                        self.highs.append(candle['high'])
                        self.lows.append(candle['low'])
                        self.closes.append(candle['close'])
                
                # Calculer les indicateurs
                if len(self.closes) > 0:
                    current_price = self.closes[-1]
                    self.result['current_price'] = current_price
                    
                    # Calcul des indicateurs
                    rsi = TechnicalIndicators.rsi(self.closes)
                    stoch_k = TechnicalIndicators.stochastic(self.highs, self.lows, self.closes)
                    atr = TechnicalIndicators.atr(self.highs, self.lows, self.closes)
                    bb_upper, bb_middle, bb_lower = TechnicalIndicators.bollinger_bands(self.closes)
                    
                    self.result['indicators'] = {
                        'rsi': rsi,
                        'stochastic_k': stoch_k,
                        'atr': atr,
                        'bollinger_upper': bb_upper,
                        'bollinger_middle': bb_middle,
                        'bollinger_lower': bb_lower
                    }
                    
                self.candles_received = True
                self.check_completion()
                
            # Positions ouvertes
            elif 'portfolio' in data:
                portfolio = data['portfolio']
                if 'contracts' in portfolio:
                    positions = portfolio['contracts']
                    self.vix75_positions = [p for p in positions if p.get('symbol') == 'R_75']
                    
                    if self.vix75_positions:
                        # Demander les détails pour chaque position
                        for pos in self.vix75_positions:
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
                for pos in self.vix75_positions:
                    if pos.get('contract_id') == contract_id:
                        contract_type = pos.get('contract_type', 'N/A')
                        
                        # Traduire le type de contrat
                        contract_display = self.translate_contract_type(contract_type)
                        
                        position_data = {
                            'contract_type_raw': contract_type,
                            'contract_type': contract_display,
                            'buy_price': pos.get('buy_price', 'N/A'),
                            'profit_percentage': details.get('profit_percentage', 'N/A')
                        }
                        self.result['positions'].append(position_data)
                        break
                
                self.positions_detailed = True
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
        if self.candles_received and self.positions_detailed:
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

def get_vix75_data():
    """Fonction pour récupérer les données VIX75"""
    API_TOKEN = "0BV3Ve4oK74HMlU"
    
    try:
        collector = DerivDataCollector(API_TOKEN)
        data = collector.collect_data()
        return data
    except Exception as e:
        return {'error': str(e)}

@app.route('/')
def vix75_data():
    """
    API endpoint pour récupérer les données VIX75
    """
    try:
        logger.info("📊 Récupération des données VIX75...")
        
        # Récupérer les données
        data = get_vix75_data()
        
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
                'symbol': 'VIX75',
                'current_price': data.get('current_price', 'N/A')
            },
            'indicators': data.get('indicators', {}),
            'positions': data.get('positions', [])
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
        'service': 'VIX75 Deriv API',
        'timestamp': time.time()
    })

if __name__ == '__main__':
    # Port pour Render (utilise la variable d'environnement PORT)
    import os
    PORT = int(os.environ.get('PORT', 5000))
    
    print("🚀 API Deriv VIX75 démarrée")
    print(f"📍 Port: {PORT}")
    
    app.run(host='0.0.0.0', port=PORT, debug=False)
