from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
    #app.run(ssl_context=('path/to/cert.pem', 'path/to/key.pem'))