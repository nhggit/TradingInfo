import os
import re
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication

import yfinance as yf
import requests
import pandas as pd
from bs4 import BeautifulSoup
from azure.ai.inference import ChatCompletionsClient
from azure.ai.inference.models import SystemMessage, UserMessage
from azure.core.credentials import AzureKeyCredential


# ============================================================
# メール送信用設定（環境変数から取得。コードに直書きしない）
# ============================================================
MAIL_CONFIG = {
    "smtp_server": os.getenv("MAIL_SMTP_SERVER", "smtp.gmail.com"),
    "smtp_port": int(os.getenv("MAIL_SMTP_PORT", "587")),
    "user": os.getenv("MAIL_USER", ""),
    "password": os.getenv("MAIL_PASS", ""),  # Gmailはアプリパスワード
    "from_addr": os.getenv("MAIL_FROM", os.getenv("MAIL_USER", "")),
    "to_addrs": [a.strip() for a in os.getenv("MAIL_TO", "").split(",") if a.strip()],
}


def send_email_with_attachment(file_path: str) -> bool:
    """
    引数で渡された 1 つのファイル（最新レポート）だけを添付して送信する。
    複数ファイル添付やディレクトリ指定は拒否する安全設計。
    """
    if not (MAIL_CONFIG["user"] and MAIL_CONFIG["password"] and MAIL_CONFIG["to_addrs"]):
        print("⚠️ メール設定が不完全です (MAIL_USER / MAIL_PASS / MAIL_TO を確認)。")
        return False

    if not file_path or not os.path.exists(file_path):
        print(f"⚠️ 送信対象のファイルが存在しません: {file_path}")
        return False

    # ディレクトリを誤って指定しても送信しない
    if os.path.isdir(file_path):
        print(f"⚠️ ディレクトリは送信できません: {file_path}")
        return False

    filename = os.path.basename(file_path)

    # --- メール本文作成 ---
    msg = MIMEMultipart()
    msg["Subject"] = f"🤖 デイリーレポート {datetime.now().strftime('%Y-%m-%d')}"
    msg["From"] = MAIL_CONFIG["from_addr"]
    msg["To"] = ", ".join(MAIL_CONFIG["to_addrs"])

    body = (
        "お疲れ様です。\n"
        "本日のプロトレーダー・デイリーレポートを添付いたします。\n\n"
        f"添付ファイル: {filename}\n"
        f"生成日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    )
    msg.attach(MIMEText(body, "plain", "utf-8"))

    # --- ★ 単一ファイルのみを添付 ---
    try:
        with open(file_path, "rb") as f:
            part = MIMEApplication(f.read(), Name=filename)
            part["Content-Disposition"] = f'attachment; filename="{filename}"'
            msg.attach(part)
    except Exception as e:
        print(f"❌ 添付ファイル読み込みエラー: {e}")
        return False

    # --- SMTP 送信 ---
    try:
        with smtplib.SMTP(MAIL_CONFIG["smtp_server"], MAIL_CONFIG["smtp_port"], timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.login(MAIL_CONFIG["user"], MAIL_CONFIG["password"])
            server.sendmail(
                MAIL_CONFIG["from_addr"],
                MAIL_CONFIG["to_addrs"],
                msg.as_string()
            )
        print(f"✅ メール送信成功: {filename} → {MAIL_CONFIG['to_addrs']}")
        return True
    except Exception as e:
        print(f"❌ メール送信エラー: {e}")
        return False


# ============================================================
# 以下、元のロジック
# ============================================================
def get_market_data():
    """主要市場データの取得"""
    tickers = {
        "^GSPC": "S&P 500",
        "^N225": "Nikkei 225",
        "USDJPY=X": "USD/JPY",
        "EURUSD=X": "EUR/USD",
        "BTC-USD": "Bitcoin",
        "GC=F": "Gold",
    }
    data_list = []
    for ticker, name in tickers.items():
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="5d")
            if len(hist) >= 2:
                current_price = hist["Close"].iloc[-1]
                prev_price = hist["Close"].iloc[-2]
                change = ((current_price - prev_price) / prev_price) * 100
                data_list.append(
                    {
                        "名称": name,
                        "現在値": f"{current_price:.4f}" if "USD" in name else f"{current_price:.2f}",
                        "前日比(%)": f"{change:+.2f}%",
                    }
                )
        except Exception as e:
            print(f"Error fetching {name}: {e}")
    return pd.DataFrame(data_list)


def get_sentiment_data():
    """Myfxbookからセンチメントを取得"""
    url = "https://www.myfxbook.com/community/outlook"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/91.0.4472.124 Safari/537.36"
        )
    }
    try:
        response = requests.get(url, headers=headers, timeout=20)
        soup = BeautifulSoup(response.content, "html.parser")
        table = soup.find("table", {"id": "outlookSymbolsTable"}) or soup.find("table")
        data = []
        if table:
            rows = table.find_all("tr")[1:]
            for row in rows:
                cols = row.find_all("td")
                if len(cols) < 2:
                    continue
                symbol = cols[0].text.strip().replace("/", "")
                if symbol in ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD"]:
                    text = row.text.replace("\n", " ")
                    short_match = re.search(r"Short\s+(\d+)%", text)
                    long_match = re.search(r"Long\s+(\d+)%", text)
                    if short_match and long_match:
                        data.append(
                            {
                                "通貨ペア": symbol,
                                "売り比率": f"{short_match.group(1)}%",
                                "買い比率": f"{long_match.group(1)}%",
                                "状態": "売り優勢" if int(short_match.group(1)) > 50 else "買い優勢",
                            }
                        )
        if not data:
            return pd.DataFrame(
                [{"通貨ペア": "市場閉場中", "売り比率": "-", "買い比率": "-", "状態": "週末のためデータなし"}]
            )
        return pd.DataFrame(data)
    except Exception as e:
        print(f"Sentiment error: {e}")
        return pd.DataFrame([{"通貨ペア": "取得失敗", "売り比率": "-", "買い比率": "-", "状態": "エラー発生"}])


def get_news_headlines():
    """主要ニュースの取得"""
    urls = ["https://finance.yahoo.com/news/rssindex", "https://feeds.bloomberg.com/markets/news.rss"]
    news_list = []
    for url in urls:
        try:
            response = requests.get(url, timeout=15)
            soup = BeautifulSoup(response.content, features="xml")
            items = soup.find_all("item")
            for item in items[:5]:
                news_list.append(item.title.text)
            if news_list:
                break
        except Exception:
            pass
    return news_list


def analyze_with_github_ai(market_data, sentiment_data, news_headlines):
    """GitHub Modelsを使用したAI解析"""
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        return "⚠️ AI解析にはGITHUB_TOKENが必要です。"

    endpoint = "https://models.inference.ai.azure.com"
    model_name = "gpt-4o"

    try:
        client = ChatCompletionsClient(
            endpoint=endpoint,
            credential=AzureKeyCredential(token),
        )
        prompt = f"""
        あなたはプロの金融アナリストです。以下のデータを基に、今日のトレード戦略を日本語で作成してください。

        【市場データ】
        {market_data.to_string()}

        【センチメント（大衆心理）】
        {sentiment_data.to_string()}

        【最新ニュース】
        {chr(10).join(news_headlines)}

        出力内容：
        1. 今日の「特筆すべき点」（市場の歪みや注目イベント）
        2. 通貨ペア別の詳細分析（特にUSD/JPYとEUR/USDは必須）
        3. 初心者へのアドバイス
        """
        response = client.complete(
            messages=[
                SystemMessage(content="あなたはプロの金融アナリストです。"),
                UserMessage(content=prompt),
            ],
            model=model_name,
            temperature=0.7,
            max_tokens=2048,
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"GitHub AI解析エラー: {e}\n(※週末やメンテナンスによりAIモデルが一時的に利用できない場合があります)"


def generate_report():
    """レポートの生成（ファイルパスを返す）"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    market_df = get_market_data()
    sentiment_df = get_sentiment_data()
    news_headlines = get_news_headlines()

    ai_analysis = analyze_with_github_ai(market_df, sentiment_df, news_headlines)

    report_content = f"# 🤖 GitHub AI搭載プロトレーダー・デイリーレポート\n"
    report_content += f"生成日時: {now}\n\n"
    report_content += "## 🧠 AIによる市場解析と戦略\n"
    report_content += f"{ai_analysis}\n\n"
    report_content += "## 👥 大衆のポジション動向（センチメント）\n"
    report_content += sentiment_df.to_markdown(index=False) + "\n\n"
    report_content += "## 📈 主要市場サマリー\n"
    report_content += market_df.to_markdown(index=False) + "\n\n"
    report_content += "\n---\n*このレポートはGitHub Modelsを利用して自動生成されました。*"

    report_path = f"daily_report_{datetime.now().strftime('%Y%m%d')}.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_content)

    return report_path  # ★ 戻り値は新規作成された単一ファイルのパス


if __name__ == "__main__":
    # 1. 今日のレポートを生成
    report_path = generate_report()
    print(f"📝 レポート生成完了: {report_path}")

    # 2. その新規ファイル 1 つのみをメール送信
    send_email_with_attachment(report_path)
