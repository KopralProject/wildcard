import os
import logging
import json
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, Filters, 
    ConversationHandler, CallbackContext, CallbackQueryHandler
)
import requests
from flask import Flask, request
import threading

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Konstanta untuk state Conversation
CF_API, ZONE_ID, DOMAIN, IP_ADDRESS, CONFIRMATION = range(5)

# Konfigurasi Cloudflare API
CF_API_URL = "https://api.cloudflare.com/client/v4/"

# In-memory storage (gunakan database di production)
user_sessions = {}

# Handler perintah /start
def start(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    welcome_text = f"""
🤖 *Wildcard Domain Bot* 🤖

Halo {user.mention_markdown_v2()}! Saya adalah bot untuk mengatur wildcard domain di Cloudflare.

📋 *Fitur yang tersedia:*
• Setup wildcard domain (*.domain.com)
• Lihat daftar domain terkonfigurasi
• Hapus konfigurasi wildcard
• Kelola multiple domain

🔧 *Perintah yang tersedia:*
/start - Memulai bot
/setup - Setup wildcard domain baru
/list - Lihat daftar domain
/delete - Hapus konfigurasi domain
/help - Bantuan penggunaan

⚠️ *Perhatian:* Pastikan Anda sudah menyiapkan API Token Cloudflare dengan izin yang sesuai.
    """
    update.message.reply_markdown_v2(welcome_text)

# Handler perintah /help
def help_command(update: Update, context: CallbackContext) -> None:
    help_text = """
📖 *Panduan Penggunaan Bot Wildcard Domain*

1. *Persiapan:*
   - Pastikan domain sudah terdaftar di Cloudflare
   - Dapatkan API Token dari Cloudflare dengan izin:
     - Zone.DNS: Edit
     - Zone.Zone: Read

2. *Setup Wildcard:*
   - Gunakan /setup untuk memulai proses
   - Ikuti langkah-langkah yang diminta bot:
     a. Masukkan Cloudflare API Token
     b. Masukkan Zone ID domain
     c. Masukkan nama domain (contoh: example.com)
     d. Masukkan IP address tujuan

3. *Perintah Lainnya:*
   - /list - Melihat daftar domain yang sudah dikonfigurasi
   - /delete - Menghapus konfigurasi wildcard

🔐 *Keamanan:*
   - Bot tidak menyimpan API Token Anda secara permanen
   - Semua data sensitif dihapus setelah proses selesai

❓ *Bantuan Tambahan:*
   Jika mengalami kendala, hubungi administrator.
    """
    update.message.reply_markdown_v2(help_text)

# Handler perintah /setup - Memulai proses setup
def setup(update: Update, context: CallbackContext) -> int:
    user_id = update.effective_user.id
    user_sessions[user_id] = {}
    
    update.message.reply_text(
        "Mari mulai proses setup wildcard domain. \n\n"
        "Pertama, masukkan **Cloudflare API Token** Anda:",
        parse_mode=ParseMode.MARKDOWN
    )
    return CF_API

# Handler untuk menerima API Token Cloudflare
def cf_api_token(update: Update, context: CallbackContext) -> int:
    user_id = update.effective_user.id
    api_token = update.message.text.strip()
    
    # Validasi dasar token
    if len(api_token) < 10:
        update.message.reply_text("Token tidak valid. Silakan masukkan kembali Cloudflare API Token:")
        return CF_API
    
    user_sessions[user_id]['cf_api_token'] = api_token
    
    # Coba mendapatkan zone list untuk validasi token
    headers = {
        'Authorization': f'Bearer {api_token}',
        'Content-Type': 'application/json'
    }
    
    try:
        response = requests.get(f"{CF_API_URL}zones", headers=headers)
        if response.status_code == 200:
            zones = response.json()['result']
            if zones:
                zones_list = "\n".join([f"• {zone['name']} (ID: {zone['id']})" for zone in zones])
                update.message.reply_text(
                    f"✅ Token valid. Berikut zone yang tersedia:\n\n{zones_list}\n\n"
                    "Sekarang masukkan **Zone ID** untuk domain yang ingin dikonfigurasi:",
                    parse_mode=ParseMode.MARKDOWN
                )
                return ZONE_ID
            else:
                update.message.reply_text(
                    "Token valid tetapi tidak ada zone yang terdaftar. "
                    "Pastikan domain sudah terdaftar di Cloudflare. Masukkan kembali API Token:"
                )
                return CF_API
        else:
            update.message.reply_text(
                "Token tidak valid atau ada masalah dengan API. Silakan masukkan kembali Cloudflare API Token:"
            )
            return CF_API
    except Exception as e:
        update.message.reply_text(
            f"Terjadi error: {str(e)}. Silakan masukkan kembali Cloudflare API Token:"
        )
        return CF_API

# Handler untuk menerima Zone ID
def zone_id(update: Update, context: CallbackContext) -> int:
    user_id = update.effective_user.id
    zone_id = update.message.text.strip()
    
    user_sessions[user_id]['zone_id'] = zone_id
    
    update.message.reply_text(
        "Masukkan **nama domain** Anda (contoh: example.com):",
        parse_mode=ParseMode.MARKDOWN
    )
    return DOMAIN

# Handler untuk menerima domain
def domain(update: Update, context: CallbackContext) -> int:
    user_id = update.effective_user.id
    domain = update.message.text.strip()
    
    # Validasi domain
    if '.' not in domain or ' ' in domain:
        update.message.reply_text("Format domain tidak valid. Silakan masukkan domain yang benar (contoh: example.com):")
        return DOMAIN
    
    user_sessions[user_id]['domain'] = domain
    
    update.message.reply_text(
        "Masukkan **IP address** tujuan untuk wildcard:",
        parse_mode=ParseMode.MARKDOWN
    )
    return IP_ADDRESS

# Handler untuk menerima IP address
def ip_address(update: Update, context: CallbackContext) -> int:
    user_id = update.effective_user.id
    ip = update.message.text.strip()
    
    # Validasi IP address sederhana
    if not (ip.replace('.', '').isdigit() and ip.count('.') == 3):
        update.message.reply_text("Format IP address tidak valid. Silakan masukkan IP address yang benar:")
        return IP_ADDRESS
    
    user_sessions[user_id]['ip_address'] = ip
    
    # Konfirmasi sebelum membuat record
    domain = user_sessions[user_id]['domain']
    keyboard = [
        [InlineKeyboardButton("✅ Ya, Buat Record", callback_data='confirm_yes')],
        [InlineKeyboardButton("❌ Batal", callback_data='confirm_no')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    update.message.reply_text(
        f"📋 Konfirmasi pembuatan wildcard record:\n\n"
        f"• Domain: *.{domain}\n"
        f"• IP Address: {ip}\n\n"
        f"Apakah Anda yakin ingin melanjutkan?",
        reply_markup=reply_markup
    )
    return CONFIRMATION

# Handler untuk konfirmasi
def confirmation(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    user_id = query.from_user.id
    query.answer()
    
    if query.data == 'confirm_yes':
        # Ambil data dari session
        api_token = user_sessions[user_id]['cf_api_token']
        zone_id = user_sessions[user_id]['zone_id']
        domain = user_sessions[user_id]['domain']
        ip = user_sessions[user_id]['ip_address']
        
        # Buat wildcard record
        headers = {
            'Authorization': f'Bearer {api_token}',
            'Content-Type': 'application/json'
        }
        
        data = {
            'type': 'A',
            'name': f'*.{domain}',
            'content': ip,
            'ttl': 1,  # Auto TTL
            'proxied': False  # Non-aktifkan proxy Cloudflare
        }
        
        try:
            response = requests.post(
                f"{CF_API_URL}zones/{zone_id}/dns_records",
                headers=headers,
                json=data
            )
            result = response.json()
            
            if response.status_code == 200 and result['success']:
                # Simpan informasi konfigurasi (dalam production, gunakan database)
                record_id = result['result']['id']
                config_key = f"config_{user_id}_{domain}"
                
                query.edit_message_text(
                    f"✅ Berhasil membuat wildcard record!\n\n"
                    f"• Domain: *.{domain}\n"
                    f"• IP Address: {ip}\n"
                    f"• Record ID: {record_id}\n\n"
                    f"Wildcard domain sekarang mengarah ke IP address yang ditentukan."
                )
            else:
                error_msg = result['errors'][0]['message'] if result.get('errors') else 'Unknown error'
                query.edit_message_text(f"❌ Gagal membuat record: {error_msg}")
        except Exception as e:
            query.edit_message_text(f"❌ Terjadi error: {str(e)}")
    else:
        query.edit_message_text("❌ Proses dibatalkan.")
    
    # Hapus session data
    if user_id in user_sessions:
        del user_sessions[user_id]
    
    return ConversationHandler.END

# Handler untuk perintah /list
def list_domains(update: Update, context: CallbackContext):
    # Dalam implementasi nyata, gunakan database untuk menyimpan konfigurasi
    user_id = update.effective_user.id
    
    # Contoh data (gantikan dengan data dari database)
    domains = ["example.com", "test.com"]
    
    if domains:
        domains_list = "\n".join([f"• {domain}" for domain in domains])
        update.message.reply_text(
            f"📋 Daftar domain yang dikonfigurasi:\n\n{domains_list}"
        )
    else:
        update.message.reply_text("Anda belum memiliki domain yang dikonfigurasi.")

# Handler untuk perintah /delete
def delete_domain(update: Update, context: CallbackContext):
    update.message.reply_text(
        "Fitur penghapusan domain akan segera hadir. "
        "Untuk saat ini, Anda dapat menghapus manual melalui dashboard Cloudflare."
    )

# Batalkan proses
def cancel(update: Update, context: CallbackContext) -> int:
    user_id = update.effective_user.id
    if user_id in user_sessions:
        del user_sessions[user_id]
    
    update.message.reply_text("❌ Proses dibatalkan.")
    return ConversationHandler.END

# Fungsi utama
def main() -> None:
    # Ambil token dari environment variable
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("Token bot tidak ditemukan! Set env var TELEGRAM_BOT_TOKEN.")
        return

    # Setup Updater dan Dispatcher
    updater = Updater(token)
    dispatcher = updater.dispatcher

    # Setup ConversationHandler untuk proses setup
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('setup', setup)],
        states={
            CF_API: [MessageHandler(Filters.text & ~Filters.command, cf_api_token)],
            ZONE_ID: [MessageHandler(Filters.text & ~Filters.command, zone_id)],
            DOMAIN: [MessageHandler(Filters.text & ~Filters.command, domain)],
            IP_ADDRESS: [MessageHandler(Filters.text & ~Filters.command, ip_address)],
            CONFIRMATION: [CallbackQueryHandler(confirmation)]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    # Tambahkan handlers
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("help", help_command))
    dispatcher.add_handler(CommandHandler("list", list_domains))
    dispatcher.add_handler(CommandHandler("delete", delete_domain))
    dispatcher.add_handler(conv_handler)

    # Jalankan bot
    updater.start_polling()
    logger.info("Bot started polling...")
    updater.idle()

if __name__ == '__main__':
    main()