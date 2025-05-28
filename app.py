from flask import Flask, request, send_file
from playwright.sync_api import sync_playwright
import tempfile
import os
import logging

# Configuration du logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

def take_screenshot(url, width=1920, height=1080):
    """
    Prend une capture d'écran avec EXACTEMENT votre code qui fonctionne
    """
    try:
        # Créer un fichier temporaire
        temp_file = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
        temp_path = temp_file.name
        temp_file.close()
        
        logger.info(f"Capture pour: {url}")
        
        # EXACTEMENT VOTRE CODE QUI FONCTIONNE
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            
            page.goto(url, wait_until="load", timeout=15000)
            page.wait_for_timeout(5000)
            
            # Taille d'écran large pour dézoom
            page.set_viewport_size({"width": width, "height": height})
            
            # Capture full page
            page.screenshot(path=temp_path, full_page=True)
            
            browser.close()
        
        logger.info("✅ Capture plein écran réussie")
        return temp_path
        
    except Exception as e:
        logger.error(f"❌ Erreur: {str(e)}")
        raise e

@app.route('/')
def screenshot():
    """
    Service ultra simple - copie exacte de votre script
    """
    try:
        # Récupérer l'URL (ou utiliser Deriv par défaut)
        url = request.args.get('url')
        
        # Si pas d'URL, utiliser l'URL Deriv R_75 par défaut
        if not url:
            url = 'https://app.deriv.com/dtrader?lang=FR&chart_type=candle&interval=15m&symbol=R_75&trade_type=multiplier&account=demo'
        
        # Paramètres optionnels
        width = int(request.args.get('width', 1920))
        height = int(request.args.get('height', 1080))
        
        logger.info(f"📸 Capture demandée pour: {url[:60]}...")
        
        # Prendre la capture avec votre méthode
        temp_path = take_screenshot(url, width, height)
        
        # Retourner l'image directement
        return send_file(
            temp_path, 
            mimetype='image/png',
            as_attachment=True,
            download_name='deriv_capture.png'
        )
        
    except Exception as e:
        return f"❌ Erreur: {str(e)}", 500

@app.route('/health')
def health():
    """Vérifier que le service fonctionne"""
    return "✅ Service Deriv actif"

# Pour Render
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)