from flask import Blueprint, render_template, request, redirect, url_for, session

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        # Here you should validate credentials with AWS Lambda API
        if username == "admin" and password == "password":  # Replace with actual authentication logic
            session["user"] = username
            return redirect(url_for("login.login"))  # Redirect to wallet page after login
        return "Invalid credentials", 401
    return render_template("login.html")

@auth_bp.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("auth.login"))