import os
import re
import json
import smtplib
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

def parse_esun_html():
    target_url = "https://www.esunbank.com/zh-tw/personal/credit-card/discount/shopInfo?sno=2100_06"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    print("正在下載網頁原始碼...")
    response = requests.get(target_url, headers=headers)
    response.encoding = 'utf-8'
    
    if response.status_code != 200:
        print(f"網頁連線失敗: {response.status_code}")
        return None, None

    soup = BeautifulSoup(response.text, 'html.parser')
    title_tags = soup.find_all('p', class_='pt-3')
    activity_list = []
    raw_promo_html_list = []  # 用來收集給 AI 分析的原始 HTML 區塊
    
    for p_tag in title_tags:
        title = p_tag.get_text(strip=True)
        if not title or "活動" not in title: 
            continue
        
        ul_tag = p_tag.find_next_sibling('ul', class_='greenBulletList')
        if not ul_tag:
            continue
            
        # 收集活動 HTML 片段供 AI 分析使用
        raw_promo_html_list.append(str(p_tag))
        raw_promo_html_list.append(str(ul_tag))
            
        li_elements = ul_tag.find_all('li')
        all_text_combined = " ".join([li.get_text(strip=True) for li in li_elements])
        
        # 擷取時間與名額
        time_pattern = r'\d{4}/\d{1,2}/\d{1,2}\s+\d{2}:\d{2}:\d{2}(?:~\d{4}/\d{1,2}/\d{1,2}\s+\d{2}:\d{2}:\d{2})?'
        times = re.findall(time_pattern, all_text_combined)
        registration_time = " / ".join(times) if times else "需見內文或免登錄"
        
        quota_pattern = r'(?:限量|名額|登錄)[^，。\n]*?([\d,]+名)'
        quotas = re.findall(quota_pattern, all_text_combined)
        quota_limit = " / ".join(list(set(quotas))) if quotas else "未明確標示限額"
        
        benefits = []
        for li in li_elements:
            text = li.get_text(strip=True)
            if any(keyword in text for keyword in ['回饋', '折', '點', '上限', '適用']):
                if "登錄辦法" not in text:
                    benefits.append(f"• {text}")
        benefits_summary = "<br>".join(benefits)

        activity_list.append({
            "name": title,
            "quota": quota_limit,
            "time": registration_time,
            "benefits": benefits_summary
        })
        
    raw_promo_html = "\n".join(raw_promo_html_list)
    return activity_list, raw_promo_html

def send_email(activities):
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD")
    
    if not gmail_user or not gmail_password:
        print("未偵測到 Gmail 憑證環境變數，跳過發信。")
        return

    msg = MIMEMultipart()
    msg['From'] = gmail_user
    msg['To'] = gmail_user 
    msg['Subject'] = f"【自動通知】玉山銀行信用卡優惠定時回報 ({datetime.now().strftime('%m/%d')})"

    html_content = f"""
    <html>
    <head>
        <style>
            table {{ border-collapse: collapse; width: 100%; font-family: sans-serif; }}
            th, td {{ border: 1px solid #dddddd; text-align: left; padding: 10px; }}
            th {{ background-color: #00a19b; color: white; }}
            tr:nth-child(even) {{ background-color: #f9f9f9; }}
            .highlight {{ color: #f8524c; font-weight: bold; }}
        </style>
    </head>
    <body>
        <h2>🎯 目前最新玉山刷卡活動一覽</h2>
        <p>更新時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        <table>
            <tr>
                <th>活動名稱</th>
                <th>限額數量</th>
                <th>登錄時間</th>
                <th>優惠核心摘要</th>
            </tr>
    """
    
    for act in activities:
        html_content += f"""
            <tr>
                <td><b>{act['name']}</b></td>
                <td class="highlight">{act['quota']}</td>
                <td>{act['time']}</td>
                <td style="font-size: 13px;">{act['benefits']}</td>
            </tr>
        """
        
    html_content += """
        </table>
        <br>
        <p>備註：本信件由 GitHub Actions 爬蟲機器人自動發送。</p>
    </body>
    </html>
    """
    
    msg.attach(MIMEText(html_content, 'html', 'utf-8'))

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(gmail_user, gmail_password)
        server.send_message(msg)
        server.quit()
        print("電子郵件已成功寄出！")
    except Exception as e:
        print(f"發信失敗: {e}")

def update_html_with_ai(raw_promo_html):
    github_token = os.environ.get("GITHUB_TOKEN")
    if not github_token:
        print("⚠️ 未在環境變數中偵測到 GITHUB_TOKEN，跳過 AI 處理與 HTML 更新。")
        return

    # 定義本次使用的模型與時間
    model_name = "gpt-4o"
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M")

    api_url = "https://models.inference.ai.azure.com/chat/completions"
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Content-Type": "application/json"
    }

    system_prompt = (
        "你是一個精密的台灣信用卡優惠分析機器。請將輸入的 HTML 活動文字，整理成 JavaScript 的 Array 格式。\n\n"
        "特別注意：\n"
        "1. 必須將同一個活動的階梯滿額禮拆解成獨立物件，並歸類在同一個 \"group\" (群組名稱，例如: 6.18年中慶) 中。\n"
        "2. 辨識 \"fixed\" (固定金額/點數) 與 \"percent\" (百分比) 兩種類型。\n"
        "3. 清除不符合當前交易計算的繁雜文字（如：名額限制、 Wallet 下載條件請精簡括號備註在活動名稱內即可）。\n"
        "4. 欄位架構必須包含: id, name, group, threshold, type, value, cap, checked (預設給 true)\n"
        "5. 只回傳一個乾淨的 JSON Array 格式，絕對不要包含任何 markdown 標籤（如 ```json 等）。\n\n"
        "輸出格式範例：\n"
        "[\n"
        "  { \"id\": \"promo_1\", \"name\": \"6.18年中慶 (滿12k送800)\", \"group\": \"6.18年中慶\", \"threshold\": 12000, \"type\": \"fixed\", \"value\": 800, \"cap\": 800, \"checked\": true },\n"
        "  { \"id\": \"promo_2\", \"name\": \"6.18年中慶 (滿26k送1800)\", \"group\": \"6.18年中慶\", \"threshold\": 26000, \"type\": \"fixed\", \"value\": 1800, \"cap\": 1800, \"checked\": true }\n"
        "]"
    )

    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"請分析以下玉山銀行活動網頁文字，並轉換成符合格式的 JSON 陣列：\n\n{raw_promo_html}"}
        ],
        "temperature": 0.1
    }

    try:
        print(f"🤖 正在呼叫 GitHub 免費 AI 模型 ({model_name}) 進行階梯群組化分析...")
        response = requests.post(api_url, json=payload, headers=headers)
        
        if response.status_code != 200:
            print(f"❌ AI 模型呼叫失敗，狀態碼: {response.status_code}, 原因: {response.text}")
            return

        ai_result = response.json()['choices'][0]['message']['content'].strip()
        
        # 清理可能不小心夾帶的 Markdown 標籤
        if ai_result.startswith("```"):
            ai_result = re.sub(r"^```json\s*", "", ai_result)
            ai_result = re.sub(r"\s*```$", "", ai_result)
        ai_result = ai_result.strip()

        # 安全性驗證：嘗試解析成 JSON 物件，確保格式百分之百正確
        parsed_json = json.loads(ai_result)
        print(f"🎉 AI 成功解析出 {len(parsed_json)} 檔細緻活動！開始熱插拔注入前端...")

        # 將物件重新排版成漂亮的縮排格式字串
        formatted_js_array = json.dumps(parsed_json, ensure_ascii=False, indent=6)

        html_path = "index.html"
        if os.path.exists(html_path):
            with open(html_path, "r", encoding="utf-8") as f:
                html_content = f.read()

            # 精準鎖定前端網頁中的三個變數宣告區
            promo_pattern = r"let promos = \[\s*[\s\S]*?\s*\];"
            model_pattern = r'let aiModel = ".*?";'
            time_pattern = r'let updateTime = ".*?";'

            if re.search(promo_pattern, html_content):
                # 1. 替換優惠活動陣列
                html_content = re.sub(promo_pattern, f"let promos = {formatted_js_array};", html_content)
                # 2. 替換模型名稱
                html_content = re.sub(model_pattern, f'let aiModel = "{model_name}";', html_content)
                # 3. 替換更新時間
                html_content = re.sub(time_pattern, f'let updateTime = "{current_time}";', html_content)

                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(html_content)
                print(f"💾 最新活動、模型({model_name})與時間({current_time})已成功重寫寫入 index.html！")
            else:
                print("❌ 錯誤：在 index.html 中找不到「let promos = [...];」結構，無法注入。")
        else:
            print("❌ 錯誤：找不到 index.html 檔案。")

    except Exception as e:
        print(f"❌ 執行 AI 處理或更新 HTML 時發生異常: {e}")

if __name__ == "__main__":
    data, raw_html = parse_esun_html()
    if data:
        # 1. 執行信件自動通報
        send_email(data)
        
        # 2. 保留原有的 JSON 存檔與變更追蹤歷史紀錄
        final_output = {
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "data": data
        }
        with open("esun_discounts.json", "w", encoding="utf-8") as f:
            json.dump(final_output, f, ensure_ascii=False, indent=4)
        print("💾 已更新本地資料庫備份 esun_discounts.json")
            
        # 3. 呼叫 GitHub AI 模型並即時動態注入改寫 index.html (包含模型名稱與時間)
        if raw_html:
            update_html_with_ai(raw_html)
