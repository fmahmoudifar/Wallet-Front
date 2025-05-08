from flask import Blueprint, render_template, session, request, redirect, url_for, jsonify
import requests
from collections import defaultdict
from config import API_URL, aws_auth
from decimal import Decimal
from datetime import datetime

layout_bp = Blueprint("layout", __name__)

# @home_bp.route("/", methods=['GET'])
# def users():
# user = session.get('user')
# if user:
#     URL = '''<li class="nav-item">
#                 <a class="nav-link" href="/">Home</a>
#             </li>
#             <li class="nav-item">
#                 <a class="nav-link" href="/wallet">Wallets</a>
#             </li>
#             <li class="nav-item">
#                 <a class="nav-link" href="/transaction">Transactions</a>
#             </li>
#             <li class="nav-item">
#                 <a class="nav-link" href="/crypto">Crypto Currencies</a>
#             </li>
#             </li>
#             <li class="nav-item">
#                 <a class="nav-link" href="/stock">Stocks</a>
#             </li>
#             <li class="nav-item">
#                 <a class="nav-link" href="/account">Account</a>
#             </li>
#             <li class="nav-item">
#                 <a class="nav-link" href="/logout">Logout</a>
#             </li>'''
# else:
#     URL = '''<li class="nav-item">
#                 <a class="nav-link" href="/">Home</a>
#             </li>        
#             <li class="nav-item">
#                 <a class="nav-link" href="/login">Login</a>
#             </li>'''