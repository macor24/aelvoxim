# -*- coding: utf-8 -*-
"""
MetaCore WeCom (WeChat Work) Integration — Test Callback Mode
=============================================================
Debug using WeCom Developer Center's "Test Callback Mode" page.
No external tools or public server needed.

Steps:
  1. Fill in config below (CORP_ID / TOKEN / ENCODING_AES_KEY)
  2. Run: python wecom_bot.py
  3. Open https://developer.work.weixin.qq.com/devtool/debug
  4. Fill the form and click "Call API"

Patent notice:
  - Internal testing only, does not affect patent novelty
  - Use test questions, do not submit real business data
"""

import sys
import os
import time
import hashlib
import base64
import json
import struct
from socket import inet_aton
from flask import Flask, request
import xml.etree.ElementTree as ET

try:
    from Crypto.Cipher import AES
except ImportError:
    from Cryptodome.Cipher import AES

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

app = Flask(__name__)

# ==================== Config (env var priority, .env file fallback) ====================

def _load_env_file(path: str = ".env"):
    """Load key=value pairs from a .env file. Pure stdlib, no dotenv dependency."""
    try:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
        if not os.path.exists(env_path):
            return
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip("\"'")
                if key not in os.environ:  # env var takes precedence
                    os.environ[key] = val
    except Exception:
        pass  # silent failure — env vars may not exist


_load_env_file()

CORP_ID = os.environ.get("WECOM_CORP_ID", "")
TOKEN = os.environ.get("WECOM_TOKEN", "")
ENCODING_AES_KEY = os.environ.get("WECOM_ENCODING_AES_KEY", "")
AGENT_ID = int(os.environ.get("WECOM_AGENT_ID", "0"))
SECRET = os.environ.get("WECOM_SECRET", "")
# ============================================================


def sha1_sort(*args):
    """SHA1 signature: sort params, concatenate, then SHA1"""
    sort_str = "".join(sorted(args))
    return hashlib.sha1(sort_str.encode("utf-8")).hexdigest()


def aes_decrypt(encoded_aes_key, encrypted_data):
    """
    AES decrypt (WeCom message encryption/decryption)
    encoded_aes_key: EncodingAESKey (base64)
    encrypted_data: Encrypted ciphertext (base64)
    Returns: Decrypted plaintext bytes

    Raises:
        ValueError: padding check failed or data format error
    """
    # Base64 decode to get AES Key (43 chars, no padding, b64decode auto-handles)
    aes_key = base64.b64decode(encoded_aes_key)
    # Base64 decode to get ciphertext
    cipher_text = base64.b64decode(encrypted_data)

    # IV is first 16 bytes of AES Key
    iv = aes_key[:16]

    # AES-256-CBC decrypt (PKCS7 padding)
    cipher = AES.new(aes_key, AES.MODE_CBC, iv)
    decrypted = bytearray(cipher.decrypt(cipher_text))

    # PKCS7 padding check: pad_len must be 1~16
    pad_len = decrypted[-1]
    if not (1 <= pad_len <= 16):
        raise ValueError("invalid PKCS7 padding length: {}".format(pad_len))
    # Verify all padding bytes match
    if any(b != pad_len for b in decrypted[-pad_len:]):
        raise ValueError("PKCS7 padding bytes mismatch")
    decrypted = bytes(decrypted[:-pad_len])

    return decrypted


def decrypt_echostr(encoding_aes_key, echostr):
    """
    Decrypt echostr (test callback verification)
    Returns decrypted plaintext string

    Raises:
        ValueError: format check failed
    """
    raw = aes_decrypt(encoding_aes_key, echostr)

    if len(raw) < 20:
        raise ValueError("decrypted data too short for echostr format")

    # WeCom format: 16 random bytes + 4 bytes msg_len + msg_content + corp_id
    # msg_len is big-endian 32-bit int (network byte order)
    msg_len = struct.unpack("!I", raw[16:20])[0]
    if msg_len < 1 or msg_len > len(raw) - 20:
        raise ValueError("invalid msg_len: {}".format(msg_len))
    msg = raw[20:20 + msg_len].decode("utf-8")
    from_corp_id = raw[20 + msg_len:].decode("utf-8")

    # Verify corpid match (integrity check)
    if from_corp_id != CORP_ID:
        print("  [WARN] corpid mismatch: expected={} got={}".format(CORP_ID, from_corp_id))

    print("  [decrypt] msg_len={}, msg={}, from_corp_id={}".format(msg_len, msg, from_corp_id))
    return msg


# ==================== Routes ====================

@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    """Unified handler: GET verification + POST message reception"""
    
    if request.method == "GET":
        return _handle_verify()
    else:
        return _handle_message()


def _handle_verify():
    """
    Handle WeCom URL verification request (GET)
    Test callback mode sends: msg_signature, timestamp, nonce, echostr
    Must verify signature + decrypt echostr before returning
    """
    msg_signature = request.args.get("msg_signature", "")
    timestamp = request.args.get("timestamp", "")
    nonce = request.args.get("nonce", "")
    echostr = request.args.get("echostr", "")
    
    print("\n" + "=" * 50)
    print("[WeCom] Verification request received (GET)")
    print("  signature: {}".format(msg_signature))
    print("  timestamp: {}".format(timestamp))
    print("  nonce:     {}".format(nonce))
    print("  echostr:   {}".format((echostr or "")[:50]))
    
    # Step 1: Verify signature (WeCom spec: SHA1(sorted(token, timestamp, nonce)))
    our_sig = sha1_sort(TOKEN, timestamp, nonce)
    print("  Our signature: {}".format(our_sig))
    
    if our_sig != msg_signature:
        print("  [ERR] Signature mismatch!")
        print("    TOKEN='{}'".format(TOKEN))
        print("    Verify Token matches the one in test callback page")
        return "signature error", 403
    
    print("  [OK] Signature verified")
    
    # Step 2: Decrypt echostr
    try:
        decrypted = decrypt_echostr(ENCODING_AES_KEY, echostr)
        print("  [OK] echostr decrypted: {}".format(decrypted))
        return decrypted
    except Exception as e:
        print("  [ERR] echostr decrypt failed: {}".format(e))
        import traceback
        traceback.print_exc()
        return "decrypt error: {}".format(e), 500


def _handle_message():
    """
    Handle WeCom push message (POST)

    WeCom callback includes msg_signature/timestamp/nonce as URL params.
    Must verify signature first, then process message body.
    """
    # Verify signature
    msg_signature = request.args.get("msg_signature", "")
    timestamp = request.args.get("timestamp", "")
    nonce = request.args.get("nonce", "")

    if not msg_signature or not timestamp or not nonce:
        print("  [WARN] POST missing signature params (may be test callback)")
    else:
        # Extract Encrypt field from XML (if present)
        xml_data = request.data.decode("utf-8")
        try:
            # Disable external entity resolution to prevent XXE (CWE-611)
            try:
                from defusedxml.ElementTree import fromstring as _safe_xml
                root = _safe_xml(xml_data)
            except ImportError:
                parser = ET.XMLParser()
                parser.entity = {}
                root = ET.fromstring(xml_data, parser)
            enc_el = root.find("Encrypt")
            encrypt_text = enc_el.text if enc_el is not None else ""
        except Exception:
            encrypt_text = ""

        sign_target = encrypt_text if encrypt_text else request.data.decode("utf-8")
        our_sig = sha1_sort(TOKEN, timestamp, nonce, sign_target)
        if our_sig != msg_signature:
            print("  [ERR] POST signature mismatch! our={} theirs={}".format(our_sig[:16], msg_signature[:16]))
            return "signature error", 403
        print("  [OK] POST signature verified")

    try:
        raw_xml = request.data.decode("utf-8")
        # Disable external entity resolution to prevent XXE (CWE-611)
        # Use defusedxml if available, otherwise restrict parser manually
        try:
            from defusedxml.ElementTree import fromstring as _safe_xml
            root = _safe_xml(raw_xml)
        except ImportError:
            parser = ET.XMLParser()
            parser.entity = {}
            root = ET.fromstring(raw_xml, parser)

        # Check for Encrypt field (production message encryption)
        enc_el = root.find("Encrypt")
        if enc_el is not None and enc_el.text:
            try:
                decrypted_raw = aes_decrypt(ENCODING_AES_KEY, enc_el.text)
                # WeCom format: 16 random + 4(msg_len) + msg_content + corpid
                if len(decrypted_raw) < 20:
                    raise ValueError("decrypted data too short")
                msg_len = struct.unpack("!I", decrypted_raw[16:20])[0]
                if msg_len < 1 or msg_len > len(decrypted_raw) - 20:
                    raise ValueError("invalid msg_len: {}".format(msg_len))
                msg_xml = decrypted_raw[20:20 + msg_len].decode("utf-8")
                root = ET.fromstring(msg_xml)
                print("  [OK] Encrypt decrypted")
            except Exception as e:
                print("  [ERR] Encrypt decrypt failed: {}".format(e))
                return "decrypt error", 500

        print("\n[WeCom] POST message received:")
        print(ET.tostring(root, encoding="unicode")[:500])

        to_user = root.find("ToUserName")
        from_user = root.find("FromUserName")
        create_time = root.find("CreateTime")
        msg_type = root.find("MsgType")
        content_el = root.find("Content")
        
        if to_user is not None and msg_type is not None:
            mtype = msg_type.text
            user = from_user.text if from_user is not None else "unknown"
            content = content_el.text if content_el is not None else ""
            
            print("[User] {} ({}) : {}".format(user, mtype, content))
            
            if mtype == "text":
                answer = handle_customer_query(content)
                
                reply = """<xml>
<ToUserName><![CDATA[{}]]></ToUserName>
<FromUserName><![CDATA[{}]]></FromUserName>
<CreateTime>{}</CreateTime>
<MsgType><![CDATA[text]]></MsgType>
<Content><![CDATA[{}]]></Content>
</xml>""".format(user, CORP_ID, int(time.time()), answer)
                
                print("[Bot] Reply: {}".format(answer[:80]))
                return reply
        
        return "success"
    
    except Exception as e:
        print("[ERR] Message handler error: {}".format(e))
        import traceback
        traceback.print_exc()
        return "success"


# ==================== Customer Service Logic ====================

def handle_customer_query(question):
    """Core CS logic: search KB, LLM polish, learn"""
    try:
        sys.path.insert(0, os.path.dirname(__file__))
        from demo_v2_deepseek import search_kb, call_llm, aelvoxim_learn
        
        print("[Bot] Searching KB: {}".format(question[:50]))
        
        answer, kb_id = search_kb(question)
        if answer:
            print("  [HIT] KB ID={}".format(kb_id))
            result = call_llm(question, context=answer)
            return result if result else answer
        
        # Miss — trigger learning
        print("  [MISS] Triggering MetaCore learning...")
        aelvoxim_learn(question)
        
        new_answer, new_kb_id = search_kb(question)
        if new_answer:
            print("  [LEARNED] Hit after learning ID={}".format(new_kb_id))
            result = call_llm(question, context=new_answer)
            return result if result else new_answer
        
        return "Learning in progress, please check back or contact support."
    
    except Exception as e:
        print("[ERR] CS logic error: {}".format(e))
        import traceback
        traceback.print_exc()
        return "System busy, please try again later or contact support."


# ==================== Main ====================

if __name__ == "__main__":
    print("=" * 60)
    print("  MetaCore WeCom Integration - Test Callback Mode")
    print("=" * 60)
    print()
    print("[*] Config check:")
    print("    WECOM_CORP_ID:          {}".format(CORP_ID[:15] + "..." if CORP_ID else "[NOT SET]!"))
    print("    WECOM_TOKEN:            {}".format("SET" if TOKEN else "[NOT SET]!"))
    print("    WECOM_ENCODING_AES_KEY: {}".format("SET" if ENCODING_AES_KEY else "[NOT SET]!"))
    print("    WECOM_AGENT_ID:         {}".format(AGENT_ID))
    print("    WECOM_SECRET:           {}".format("SET" if SECRET else "[NOT SET]"))
    print()
    
    if not ENCODING_AES_KEY:
        print("[!!!] WARNING: WECOM_ENCODING_AES_KEY not set!")
        print("      Configure WeCom credentials via env vars or .env file")
        print()
    
    print("[*] Usage:")
    print("    1. Ensure this service is running (localhost:5000)")
    print("    2. Open: https://developer.work.weixin.qq.com/devtool/debug")
    print("    3. Fill: URL=http://localhost:5000/webhook")
    print("           Token={}".format(TOKEN))
    print("           EncodingAESKey=<same as config above>")
    print("           EchoStr=12345678901234567890123")
    print("           ToUserName={}".format(CORP_ID))
    print("    4. Click Call API")
    print()
    print("[*] Patent notice: Internal testing, does not affect patent novelty")
    print("=" * 60)
    print()
    
    app.run(host="0.0.0.0", port=5000, debug=False)
