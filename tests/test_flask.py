from fidal_cds_tool import app
with app.app_context():
    with app.test_client() as c:
        resp = c.get('/api/proiezione/build?anno=2026&tipo_attivita=P&sesso=F&categoria=CF&regione=LOM&nazionalita=0&vento=2&limite=100&societa=BS318')
        for line in resp.iter_encoded():
            data = line.decode('utf-8')
            if 'ottimale Trovato' in data or '"sel":' in data:
                print(data[:500])
