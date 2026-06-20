with open('fidal_cds_tool.py', 'r', encoding='utf-8') as f:
    text = f.read()

route = '''
@app.route('/api/ottimizza', methods=['POST'])
def api_ottimizza():
    from core.cds_optimizer import CdsOptimizer
    try:
        payload = request.get_json()
        results = payload.get('data', [])
        cat = payload.get('categoria', 'CF')
        
        opt = CdsOptimizer.compute_optimal(results, cat)
        return jsonify({'ok': True, 'optimal': opt})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500
'''

if '@app.route(' + "'/api/ottimizza'" + ')' not in text:
    text = text.replace('@app.route(\'/\')\ndef index():', route + '\n@app.route(\'/\')\ndef index():')
    with open('fidal_cds_tool.py', 'w', encoding='utf-8') as f:
        f.write(text)
    print('API route added!')
