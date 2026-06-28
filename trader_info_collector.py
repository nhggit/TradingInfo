import os
import re
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

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


# ============================================================
# Markdown → HTML 変換（軽量・依存ゼロ）
# ============================================================
def markdown_to_html(md: str) -> str:
    """
    レポート用の最低限 Markdown → HTML 変換。
    外部ライブラリ不使用で GitHub Actions でも動作する。
    対応: 見出し(#〜###) / 太字 / 水平線 / テーブル / 段落
    """
    lines = md.split("\n")
    html_lines = []
    in_table = False

    for line in lines:
        # 水平線
        if re.match(r"^-{3,}$", line.strip()):
            if in_table:
                html_lines.append("</table>")
                in_table = False
            html_lines.append("<hr>")
            continue

        # 見出し
        h_match = re.match(r"^(#{1,3})\s+(.*)", line)
        if h_match:
            if in_table:
                html_lines.append("</table>")
                in_table = False
            level = len(h_match.group(1))
            text = h_match.group(2)
            html_lines.append(f"<h{level}>{text}</h{level}>")
            continue

        # テーブル行（| で始まる行）
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            # セパレータ行（|---|---| など）はスキップ
            if all(re.match(r"^:?-+:?$", c) for c in cells):
                continue
            if not in_table:
                html_lines.append('<table border="1" cellpadding="6" cellspacing="0" '
                                  'style="border-collapse:collapse; font-size:13px;">')
                # 最初の行をヘッダーとして扱う
                row_html = "".join(f"<th style='background:#2c3e50;color:#fff;'>{c}</th>" for c in cells)
                html_lines.append(f"<tr>{row_html}</tr>")
                in_table = True
            else:
                row_html = "".join(f"<td>{c}</td>" for c in cells)
                html_lines.append(f"<tr>{row_html}</tr>")
            continue

        # テーブル終了
        if in_table:
            html_lines.append("</table>")
            in_table = False

        # 空行
        if not line.strip():
            html_lines.append("<br>")
            continue

        # 太字 **text**
        line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)

        # 通常段落
        html_lines.append(f"<p style='margin:4px 0;'>{line}</p>")

    if in_table:
        html_lines.append("</table>")

    return "\n".join(html_lines)


def build_html_email(report_md: str) -> str:
    """レポートMarkdownをメール用HTML全体にラップする"""
    body_html = markdown_to_html(report_md)
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<style>
  body      {{ font-family: 'Helvetica Neue', Arial, sans-serif; font-size: 14px;
               color: #222; background: #f5f5f5; margin: 0; padding: 20px; }}
  .container{{ max-width: 800px; margin: 0 auto; background: #fff;
               border-radius: 8px; padding: 30px;
               box-shadow: 0 2px 8px rgba(0,0,0,.12); }}
  h1        {{ color: #1a252f; border-bottom: 3px solid #2c3e50; padding-bottom: 8px; }}
  h2        {{ color: #2c3e50; border-left: 4px solid #3498db; padding-left: 10px; }}
  h3        {{ color: #34495e; }}
  table     {{ width: 100%; margin: 12px 0; }}
  tr:nth-child(even) td {{ background: #f0f4f8; }}
  hr        {{ border: none; border-top: 1px solid #ddd; margin: 20px 0; }}
  .footer   {{ font-size: 11px; color: #999; text-align: center; margin-top: 20px; }}
</style>
</head>
<body>
<div class="container">
{body_html}
<div class="footer">このレポートはGitHub Modelsを利用して自動生成されました。</div>
</div>
</body>
</html>"""


# ============================================================
# メール送信（本文にHTMLを埋め込む）
# ============================================================
def send_email_as_html(report_md: str) -> bool:
    """
    レポートMarkdownをHTMLに変換し、メール本文として送信する。
    ファイル添付・ファイル生成は一切行わない。
    """
    if not (MAIL_CONFIG["user"] and MAIL_CONFIG["password"] and MAIL_CONFIG["to_addrs"]):
        print("⚠️ メール設定が不完全です (MAIL_USER / MAIL_PASS / MAIL_TO を確認)。")
        return False

    today = datetime.now().strftime("%Y-%m-%d")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🤖 デイリーレポート {today}"
    msg["From"] = MAIL_CONFIG["from_addr"]
    msg["To"] = ", ".join(MAIL_CONFIG["to_addrs"])

    # プレーンテキスト（フォールバック用）
    plain_text = re.sub(r"<[^>]+>", "", report_md)
    msg.attach(MIMEText(plain_text, "plain", "utf-8"))

    # HTML本文（優先表示）
    html_body = build_html_email(report_md)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

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
        print(f"✅ メール送信成功 → {MAIL_CONFIG['to_addrs']}")
        return True
    except Exception as e:
        print(f"❌ メール送信エラー: {e}")
        return False


# ============================================================
# データ取得・AI解析
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


# ============================================================
# レポート生成（文字列を返す。ファイル保存しない）
# ============================================================
def generate_report() -> str:
    """レポートをMarkdown文字列として返す（ファイル保存なし）"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    market_df = get_market_data()
    sentiment_df = get_sentiment_data()
    news_headlines = get_news_headlines()

    ai_analysis = analyze_with_github_ai(market_df, sentiment_df, news_headlines)

    report = (
        f"# 🤖 GitHub AI搭載プロトレーダー・デイリーレポート\n"
        f"生成日時: {now}\n\n"
        f"## 🧠 AIによる市場解析と戦略\n"
        f"{ai_analysis}\n\n"
        f"## 👥 大衆のポジション動向（センチメント）\n"
        f"{sentiment_df.to_markdown(index=False)}\n\n"
        f"## 📈 主要市場サマリー\n"
        f"{market_df.to_markdown(index=False)}\n\n"
        "---\n"
        "*このレポートはGitHub Modelsを利用して自動生成されました。*"
    )
    return report


# ============================================================
# エントリーポイント
# ============================================================
if __name__ == "__main__":
    print("📊 レポート生成中...")
    report_md = generate_report()
    print("📝 レポート生成完了。メール送信中...")
    send_email_as_html(report_md)
