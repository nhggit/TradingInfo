import yfinance as yf
import requests
from bs4 import BeautifulSoup
import pandas as pd
from datetime import datetime
import os

def get_market_data():
    """主要市場データの取得"""
    tickers = {
        "^GSPC": "S&P 500",
        "^N225": "Nikkei 225",
        "USDJPY=X": "USD/JPY",
        "BTC-USD": "Bitcoin",
        "GC=F": "Gold"
    }
    
    data_list = []
    for ticker, name in tickers.items():
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="2d")
            if len(hist) >= 2:
                current_price = hist['Close'].iloc[-1]
                prev_price = hist['Close'].iloc[-2]
                change = ((current_price - prev_price) / prev_price) * 100
                data_list.append({
                    "名称": name,
                    "現在値": f"{current_price:.2f}",
                    "前日比(%)": f"{change:+.2f}%"
                })
        except Exception as e:
            print(f"Error fetching {name}: {e}")
            
    return pd.DataFrame(data_list)

def get_news_headlines():
    """主要ニュースの取得 (Yahoo Finance RSSを使用)"""
    # 複数のRSSフィードを試行
    urls = [
        "https://finance.yahoo.com/news/rssindex",
        "https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best",
        "https://feeds.bloomberg.com/markets/news.rss"
    ]
    
    news_list = []
    for url in urls:
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, features="xml")
                items = soup.find_all('item')
                for item in items[:5]:
                    news_list.append({
                        "タイトル": item.title.text if item.title else "No Title",
                        "リンク": item.link.text if item.link else "#",
                        "公開日時": item.pubDate.text if item.pubDate else "Unknown"
                    })
                if news_list:
                    break
        except Exception as e:
            print(f"Error fetching news from {url}: {e}")
            
    return pd.DataFrame(news_list)

def generate_report():
    """レポートの生成"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    market_df = get_market_data()
    news_df = get_news_headlines()
    
    report_content = f"# 📊 プロトレーダー向けデイリーマーケットレポート\n"
    report_content += f"生成日時: {now}\n\n"
    
    report_content += "## 📈 主要市場サマリー\n"
    if not market_df.empty:
        report_content += market_df.to_markdown(index=False) + "\n\n"
    else:
        report_content += "データ取得に失敗しました。\n\n"
        
    report_content += "## 📰 最新ニュースヘッドライン\n"
    if not news_df.empty:
        for _, row in news_df.iterrows():
            report_content += f"- [{row['タイトル']}]({row['リンク']}) ({row['公開日時']})\n"
    else:
        report_content += "ニュース取得に失敗しました。\n\n"
        
    report_content += "\n---\n*このレポートは自動生成されました。トレードの判断は自己責任でお願いいたします。*"
    
    report_path = f"/home/ubuntu/daily_report_{datetime.now().strftime('%Y%m%d')}.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_content)
    
    return report_path

if __name__ == "__main__":
    print("情報収集を開始します...")
    path = generate_report()
    print(f"レポートが生成されました: {path}")
