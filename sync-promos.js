const fs = require('fs');
const path = require('path');
const axios = require('axios');
const cheerio = require('cheerio');

// 1. 設定玉山銀行優惠網頁 URL
const TARGET_URL = 'https://www.esunbank.com.tw/zh-tw/personal/credit-card/discount/shopInfo?sno=2100_06';

// 2. 獲取 GitHub 免費 AI 模型的 API 金鑰與 Endpoint
// GitHub Actions 環境中自帶的 GITHUB_TOKEN 或自訂的秘鑰
const GITHUB_TOKEN = process.env.GITHUB_TOKEN; 
const API_URL = 'https://models.inference.ai.azure.com/chat/completions'; // GitHub Models API 端點

async function main() {
  try {
    console.log('⚡ 正在從玉山銀行抓取最新活動網頁原始碼...');
    const response = await axios.get(TARGET_URL, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
      }
    });

    const $ = cheerio.load(response.data);
    
    // 根據您觀察到的特徵，抓取包含活動細節的特定 DOM 區塊
    // 這裡使用包含活動列表的容器或直接抓取特定的 p 與 ul 標籤
    let rawText = '';
    $('p.pt-3, ul.greenBulletList').each((i, el) => {
      rawText += $(el).html() + '\n';
    });

    if (!rawText.trim()) {
      throw new Error('未能抓取到任何活動文字，可能網頁結構有變動。');
    }

    console.log('✅ 網頁原始碼抓取成功，正在呼叫 AI 進行階梯群組化分析...');

    // 3. 呼叫 GitHub 自帶的免費 AI 模型 (這裡以 GPT-4o / Llama 3 等模型為例)
    const systemPrompt = `
      你是一個精密的台灣信用卡優惠分析機器人。
      請將輸入的 HTML 活動文字，整理成 JavaScript 的 Array 格式。
      
      特別注意：
      1. 必須將同一個活動的階梯滿額禮歸類在同一個 "group" (群組) 中。
      2. 辨識 "fixed" (固定金額/點數) 與 "percent" (百分比) 兩種類型。
      3. 清除不符合當前交易的資訊（如：名額限制或 Wallet 下載條件請精簡成備註在活動名稱中）。
      4. 只回傳一個乾淨的 JSON Array 格式，不要包含 markdown 標籤（如 \`\`\`json 等）。
      
      輸出格式範例：
      [
        { "id": "promo_1", "name": "6.18年中慶 (滿12k送800)", "group": "6.18年中慶", "threshold": 12000, "type": "fixed", "value": 800, "cap": 800, "checked": true },
        { "id": "promo_2", "name": "6.18年中慶 (滿26k送1800)", "group": "6.18年中慶", "threshold": 26000, "type": "fixed", "value": 1800, "cap": 1800, "checked": true }
      ]
    `;

    const aiResponse = await axios.post(
      API_URL,
      {
        model: 'gpt-4o', // GitHub Models 提供的免費高性能模型
        messages: [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: `請分析以下玉山銀行活動網頁文字，並轉換成 JSON 陣列：\n\n${rawText}` }
        ],
        temperature: 0.1
      },
      {
        headers: {
          'Authorization': `Bearer ${GITHUB_TOKEN}`,
          'Content-Type': 'application/json'
        }
      }
    );

    let cleanJsonText = aiResponse.data.choices[0].message.content.trim();
    // 移除可能存在的 Markdown 包裝
    cleanJsonText = cleanJsonText.replace(/^```json/, '').replace(/```$/, '').trim();

    // 驗證解析出的 JSON 格式
    const parsedData = JSON.parse(cleanJsonText);
    console.log('🎉 AI 分析完成！共整理出', parsedData.length, '檔活動。');

    // 4. 讀取目前的 index.html 並將新活動注入
    const htmlPath = path.join(__dirname, 'index.html');
    let htmlContent = fs.readFileSync(htmlPath, 'utf8');

    // 使用正規表達式定位「let promos = [...];」這一行，並進行動態替換
    const promoRegex = /let promos = \[\s*[\s\S]*?\s*\];/;
    const replacement = `let promos = ${JSON.stringify(parsedData, null, 2)};`;

    if (promoRegex.test(htmlContent)) {
      htmlContent = htmlContent.replace(promoRegex, replacement);
      fs.writeFileSync(htmlPath, htmlContent, 'utf8');
      console.log('💾 成功將最新活動寫入 index.html 檔案中！');
    } else {
      throw new Error('在 index.html 中找不到 let promos 變數定義，無法注入。');
    }

  } catch (error) {
    console.error('❌ 執行同步時發生錯誤:', error.message);
    process.exit(1);
  }
}

main();
