import requests
import pandas as pd
from datetime import datetime
import os
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from bs4 import BeautifulSoup

# 設定：主要通貨ペアの名称マッピング（Financial Tradersレポート用）
TFF_MAPPING = {
    "JPY": "JAPANESE YEN",
    "EUR": "EURO CURRENCY",
    "GBP": "BRITISH POUND",
    "AUD": "AUSTRALIAN DOLLAR",
    "CAD": "CANADIAN DOLLAR"
}

# === メール送信用設定（環境変数から取得推奨） ===
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
                html_lines.append(
                    '<table border="1" cellpadding="6" cellspacing="0" '
                    'style="border-collapse:collapse; font-size:13px;">'
                )
                row_html = "".join(
                    f"<th style='background:#1a3a5c;color:#fff;'>{c}</th>" for c in cells
                )
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
  h1        {{ color: #0d2137; border-bottom: 3px solid #1a3a5c; padding-bottom: 8px; }}
  h2        {{ color: #1a3a5c; border-left: 4px solid #2980b9; padding-left: 10px; }}
  h3        {{ color: #2c3e50; }}
  table     {{ width: 100%; margin: 12px 0; }}
  tr:nth-child(even) td {{ background: #eaf2fb; }}
  hr        {{ border: none; border-top: 1px solid #ddd; margin: 20px 0; }}
  .footer   {{ font-size: 11px; color: #999; text-align: center; margin-top: 20px; }}
</style>
</head>
<body>
<div class="container">
{body_html}
<div class="footer">このレポートは自動生成されました。</div>
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
    msg["Subject"] = f"🐋 クジラ便乗シグナル・レポート {today}"
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
# データ取得・シグナル計算
# ============================================================
def get_latest_cot_data():
    """CFTCの閲覧用ページ（HTML）から最新のTFFデータをスクレイピング"""
    url = "https://www.cftc.gov/dea/futures/financial_lf.htm"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            text = response.text
            sections = text.split("---------------------------------------------------------------------------------")
            results = []
            for section in sections:
                market_match = re.search(r'([A-Z\s]+) - CHICAGO MERCANTILE EXCHANGE', section)
                if market_match:
                    market_name = market_match.group(1).strip()
                    lev_match = re.search(r'Leveraged Funds\s+\|\s+([\d,]+)\|\s+([\d,]+)\|', section)
                    if lev_match:
                        long_val = int(lev_match.group(1).replace(',', ''))
                        short_val = int(lev_match.group(2).replace(',', ''))
                        results.append({
                            "Market": market_name,
                            "Long": long_val,
                            "Short": short_val
                        })
            return pd.DataFrame(results)
        return None
    except Exception as e:
        print(f"COTデータ取得エラー: {e}")
        return None


def get_sentiment_data():
    """Myfxbookからセンチメントを取得"""
    url = "https://www.myfxbook.com/community/outlook"
    headers = {"User-Agent": "Mozilla/5.0"}
    data = []
    try:
        response = requests.get(url, headers=headers, timeout=20)
        soup = BeautifulSoup(response.content, 'html.parser')
        text = soup.get_text(separator=' ')
        for sym in ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD"]:
            pattern = rf'{sym}.*?Short\s+(\d+)%.*?Long\s+(\d+)%'
            match = re.search(pattern, text, re.DOTALL)
            if match:
                data.append({
                    "Symbol": sym,
                    "Retail_Short": int(match.group(1)),
                    "Retail_Long": int(match.group(2))
                })
        return data
    except Exception as e:
        print(f"センチメント取得エラー: {e}")
        return []


def calculate_signals(cot_df, sentiment_list):
    signals = []
    if cot_df is None or cot_df.empty:
        return pd.DataFrame()
    for sent in sentiment_list:
        symbol = sent['Symbol']
        base_curr = symbol[:3]
        target_curr = base_curr if base_curr != "USD" else symbol[3:]
        target_name = TFF_MAPPING.get(target_curr)
        if target_name:
            mask = cot_df['Market'].str.contains(target_name, case=False, na=False)
            if mask.any():
                cot_row = cot_df[mask].iloc[0]
                whale_long = float(cot_row['Long'])
                whale_short = float(cot_row['Short'])
                whale_ratio = (whale_long / (whale_long + whale_short)) * 100
                whale_bias = "Bullish" if whale_ratio > 55 else ("Bearish" if whale_ratio < 45 else "Neutral")
                retail_bias = "Bearish" if sent['Retail_Long'] > 60 else ("Bullish" if sent['Retail_Short'] > 60 else "Neutral")
                status = "Strong Buy" if whale_bias == "Bullish" and retail_bias == "Bullish" else \
                         "Strong Sell" if whale_bias == "Bearish" and retail_bias == "Bearish" else \
                         "Wait"
                if symbol == "USDJPY":
                    if status == "Strong Buy": status = "Strong Sell"
                    elif status == "Strong Sell": status = "Strong Buy"
                signals.append({
                    "ペア": symbol,
                    "クジラ(Long%)": f"{whale_ratio:.1f}%",
                    "大衆(Long%)": f"{sent['Retail_Long']}%",
                    "判定": status
                })
    return pd.DataFrame(signals)


# ============================================================
# レポート生成（文字列を返す。ファイル保存しない）
# ============================================================
def generate_report(signal_df: pd.DataFrame) -> str:
    """レポートをMarkdown文字列として返す（ファイル保存なし）"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    report = f"# 🐋 クジラ便乗シグナル・レポート\n生成日時: {now}\n\n"
    report += "## 📊 総合シグナル判定\n"

    if not signal_df.empty:
        report += signal_df.to_markdown(index=False) + "\n\n"
    else:
        report += "現在、有効なシグナルはありません。週末やデータ更新待ちの可能性があります。\n\n"

    report += (
        "## 💡 判定ロジック\n"
        "- **Strong Buy**: クジラが買い、大衆が売っている（踏み上げ期待）\n"
        "- **Strong Sell**: クジラが売り、大衆が買っている（投げ売り期待）\n\n"
        "---\n"
        "*このレポートは自動生成されました。*"
    )
    return report


# ============================================================
# エントリーポイント
# ============================================================
if __name__ == "__main__":
    print("データ取得開始...")
    cot_data = get_latest_cot_data()
    sentiment = get_sentiment_data()
    print(f"COTデータ取得結果: {'成功' if cot_data is not None else '失敗'}")
    print(f"センチメント取得数: {len(sentiment)}")

    signal_df = calculate_signals(cot_data, sentiment)
    print("シグナル計算完了。メール送信中...")

    report_md = generate_report(signal_df)
    send_email_as_html(report_md)
