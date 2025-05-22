#!/usr/bin/env python
# -*- coding: utf-8 -*-
# === Required Library Installation Command ===
# py -3.12 pip install python-telegram-bot[job-queue] openai requests jdatetime beautifulsoup4 scikit-learn lxml tweepy

# === Imports ===
import os
import time
import re
import datetime
import jdatetime
import pytz
import requests
from bs4 import BeautifulSoup
from openai import OpenAI
from telegram import Update
from collections import Counter
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    JobQueue,
)
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from KEYS import *  # Contains sensitive API keys and tokens

# === Configuration Constants ===
DIGIATO_LINKS_FILE = "digiato_sent_links.txt"  # File to store sent Digiato article links
TELEGRAM_TOKEN = ttoken
OPENAI_API_KEY = oaien
GROUP_ID = id

# === OpenAI Client Initialization ===
client = OpenAI(api_key=OPENAI_API_KEY)

# === Local AI Cache Initialization ===
training_data = []  # Stores Q&A pairs
local_texts = []  # Cached messages for local retrieval
local_ai_ready = False
latest_links_digiato = []  # Cache for latest fetched links
vectorizer = TfidfVectorizer()  # TF-IDF vectorizer
tfidf_matrix = None  # Matrix for similarity matching

# === List of Inappropriate Words (Farsi & English) ===
BAD_WORDS = set([...])  # Removed for brevity in comment

# === Weekly Calendar Image Filenames ===
day_images = ["sat.png", "sun.png", "mon.png", "tue.png", "wed.png", "thur.png", "fri.png"]

# === Extract Top Hashtags from Text ===
def extract_hashtag(text, num_hashtags=2):
    print("def extract_hashtag")
    stop_words = {...}  # Common Persian stop words
    words = text.replace('،', ' ').replace('.', ' ').replace('!', ' ').replace('؟', ' ').split()
    filtered_words = [word for word in words if word not in stop_words and word.isalpha()]
    word_freq = Counter(filtered_words)
    top_words = [word for word, _ in word_freq.most_common(min(num_hashtags, len(word_freq)))]
    hashtags = [f"#{word}" for word in top_words]
    return hashtags

# === Welcomes New Members to Group ===
async def greet_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("greet_new_member")
    for member in update.message.new_chat_members:
        await update.message.reply_text(f"\U0001F389 همراه گرامی {member.full_name} به گروه خوش آمدید.")

# === Check for Inappropriate Words in Text ===
def contains_bad_words(text):
    return any(re.search(rf"\b{re.escape(word)}\b", text, flags=re.IGNORECASE) for word in BAD_WORDS)

# === Add New Bad Word to List ===
async def add_bad_word(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        word = context.args[0].strip()
        BAD_WORDS.add(word)
        await update.message.reply_text(f"✅ کلمه '{word}' اضافه شد.")
    else:
        await update.message.reply_text("❌ لطفاً یک کلمه وارد کنید.")

# === Remove Bad Word from List ===
async def remove_bad_word(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("remove_bad_word")
    if context.args:
        word = context.args[0].strip()
        if word in BAD_WORDS:
            BAD_WORDS.remove(word)
            await update.message.reply_text(f"🗑 کلمه '{word}' حذف شد.")
        else:
            await update.message.reply_text(f"❌ کلمه '{word}' در لیست نیست.")
    else:
        await update.message.reply_text("❌ لطفاً یک کلمه وارد کنید.")

# === Display List of All Bad Words ===
async def list_bad_words(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📌 کلمات ممنوع: " + ", ".join(BAD_WORDS))

# === Retrieve Closest Local Response using TF-IDF Similarity ===
def get_local_response_tfidf(user_input):
    if not local_ai_ready or tfidf_matrix is None:
        return None
    try:
        input_vec = vectorizer.transform([user_input])
        scores = cosine_similarity(input_vec, tfidf_matrix)[0]
        max_index = scores.argmax()
        if scores[max_index] > 0.3:
            return local_texts[max_index]["a"]
    except Exception as e:
        pass 
    return None

# === Handle User Messages to Bot ===
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("handle_message")
    global local_ai_ready, tfidf_matrix
    raw_text = update.message.text.strip()
    is_reply_to_bot = (
        update.message.reply_to_message
        and update.message.reply_to_message.from_user
        and update.message.reply_to_message.from_user.is_bot
    )

    # === Bad Word Filtering ===
    if contains_bad_words(raw_text):
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=update.message.message_id)
            username = (
                f"@{update.effective_user.username}" if update.effective_user.username else update.effective_user.full_name
            )
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"⚠️ کاربر {username} پیام شما به علت استفاده از کلمات ممنوعه حذف شد.",
            )
        except Exception as e:
            pass 
        return

    # === Only Respond to Messages Triggering the Bot ===
    if not raw_text.startswith("#بات") and not is_reply_to_bot:
        return

    # === Extract User Message ===
    user_msg = raw_text.replace("#بات", "", 1).strip()
    if update.message.reply_to_message:
        user_msg = f"{update.message.reply_to_message.text}\n{user_msg}"

    # === Use Local AI Cache First ===
    if local_ai_ready:
        local_reply = get_local_response_tfidf(user_msg)
        if local_reply:
            await update.message.reply_text(local_reply)
            return

    # === Fallback to OpenAI API ===
    try:
        chat_response = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": user_msg}],
        )
        ai_reply = chat_response.choices[0].message.content
        await update.message.reply_text(ai_reply)

        # === Save Response to Local Memory ===
        training_data.append({"q": user_msg, "a": ai_reply})
        local_texts.append({"q": user_msg, "a": ai_reply})
        if len(local_texts) > 1:
            tfidf_matrix = vectorizer.fit_transform([t["q"] for t in local_texts])
            local_ai_ready = True
    except Exception as e:
        await update.message.reply_text("❌ خطا در دریافت پاسخ. لطفاً دوباره سعی کنید.")

# === Fetch Latest Digiato Article Links ===
def fetch_digiato_links():
    print("fetch_digiato_links")
    try:
        html = requests.get("https://digiato.com/").text
        soup = BeautifulSoup(html, "html.parser")
        anchors = soup.select('a.rowCard__title[href^="https://digiato.com/"]')
        links = []
        for a in anchors:
            href = a["href"]
            if href.startswith("https://digiato.com/") and href not in links:
                links.append(href)
            if len(links) >= 5:
                break
        return links
    except Exception as e:
        return []

# === Extract Keywords from Digiato Article URL ===
def extract_keywords_from_url(url):
    print("extract_keywords_from_url")
    base_url = "https://digiato.com/"
    if url.startswith(base_url):
        path = url[len(base_url):].strip('/')
    else:
        path = url
    keywords = path.replace('-', ' ').split()
    return ' '.join(keywords)

# === Generate Farsi Summary from Keywords Using OpenAI ===
def summarize_article(url):
    print("summarize_article")
    try:
        keywords = extract_keywords_from_url(url)
        prompt = f"""..."""  # Prompt content as-is
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=150,
        )
        summary = response.choices[0].message.content.strip()
        telegram_summary = f"{summary}\n{url}"
        return telegram_summary
    except Exception as e:
        return "", ""

# === Load Already Sent Digiato Links from File ===
def load_digiato_sent_links():
    if not os.path.exists(DIGIATO_LINKS_FILE):
        return set()
    with open(DIGIATO_LINKS_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

# === Save New Sent Links to File ===
def save_digiato_sent_links(links):
    with open(DIGIATO_LINKS_FILE, "a", encoding="utf-8") as f:
        for link in links:
            f.write(link + "\n")

# === Periodic Job: Send New Digiato Summaries ===
async def send_digiato_updates(context: ContextTypes.DEFAULT_TYPE):
    print("Send Digiato updates")
    current_links = fetch_digiato_links()
    sent_links = load_digiato_sent_links()
    new_links = [link for link in current_links if link not in sent_links]
    for link in new_links:
        telegram_summary = ""
        try:
            telegram_summary, _ = summarize_article(link)
        except Exception as e:
            pass 
        try:
            if telegram_summary:
                await context.bot.send_message(chat_id=GROUP_ID, text=telegram_summary)
                print(f"Sent to Telegram")
            else:
                await context.bot.send_message(chat_id=GROUP_ID, text=link)
                print(f"Sent link to Telegram")
            save_digiato_sent_links([link])
        except Exception as e:
            pass 

# === Periodic Job: Send Persian Historical Tech Events with Image ===
async def send_calendar(context: ContextTypes.DEFAULT_TYPE):
    print("Sending calendar update")
    timezone = pytz.timezone("Asia/Tehran")
    greg_today = datetime.datetime.now(timezone).date()
    weekday = greg_today.weekday()
    today = jdatetime.date.fromgregorian(date=greg_today)

    day_image_index = (weekday + 2) % len(day_images)
    day_image = day_images[day_image_index]
    day_image_path = os.path.abspath(day_image)

    try:
        prompt = f"..."
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}],
            timeout=60
        )
        events = response.choices[0].message.content.strip()
    except Exception as e:
        print(f"[OpenAI Error] {str(e)}")
        events = "❗️رویدادی یافت نشد."
    caption = f"\U0001F4C5 امروز {today} (معادل {greg_today})\n\U0001F4D6 مناسبت‌ها:\n{events}"
    try:
        with open(day_image_path, "rb") as img:
            await context.bot.send_photo(chat_id=GROUP_ID, photo=img, caption=caption)
            print("Sent calendar to Telegram")
    except Exception as e:
        pass

# === Bot Start Command Handler ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("BOT Started")
    await update.message.reply_text("\U0001F916 Bot is up and ready. Use #بات to ask me anything!")

# === Command to Show Chat ID ===
async def show_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("Show chat ID command")
    await update.message.reply_text(f"Chat ID is: {update.effective_chat.id}")

# === Main Bot Execution Function ===
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    job_queue = app.job_queue

    # Register command and message handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addbadword", add_bad_word))
    app.add_handler(CommandHandler("removebadword", remove_bad_word))
    app.add_handler(CommandHandler("listbadwords", list_bad_words))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, greet_new_member))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.add_handler(MessageHandler(filters.Regex(r"/chatid"), show_chat_id))

    # Register periodic tasks
    job_queue.run_repeating(send_calendar, interval=43200, first=2)  # Every 12 hours
    job_queue.run_repeating(send_digiato_updates, interval=1800, first=48)  # Every 30 minutes

    # Start bot polling loop
    app.run_polling()

# === Entry Point ===
if __name__ == "__main__":
    main()
