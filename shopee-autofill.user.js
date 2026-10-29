// ==UserScript==
// @name         Shopee 出品の自動入力（出品名スキャナー）
// @namespace    https://github.com/test90654/figure-listing-scanner
// @version      1.4.0
// @description  出品名スキャナーの「出品待ち」に送った商品を、Shopee Seller Centre の商品追加画面に入力する
// @match        https://seller.shopee.sg/portal/product/*
// @match        https://seller.shopee.tw/portal/product/*
// @match        https://seller.shopee.co.th/portal/product/*
// @match        https://shopee.sg/search*
// @match        https://shopee.tw/search*
// @match        https://shopee.co.th/search*
// @grant        GM_xmlhttpRequest
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_addValueChangeListener
// @connect      __SCANNER_HOST__
// ==/UserScript==

// 入力するだけで、保存や公開のボタンは押さない。内容を確かめてから自分で公開する。
(function () {
    "use strict";

    // 相場の検索ページと Seller Centre の間で、選んだ価格を受け渡す
    const MARKET_KEY = "fls_market_price";
    // 商品名の後ろに付けている定型の言葉は検索の邪魔になるので外す
    const MARKET_NOISE = /\b(Figure|Authentic|Japan|Prize|New|Banpresto|SEGA|Taito|FuRyu|Bandai|Namco|Spirits|Collectible|Statue)\b/gi;
    const MARKET_WORDS = 6;
    const PRICE_RE = /[$฿]\s?([\d,]+(?:\.\d+)?)/g;

    const panelStyle = {
        position: "fixed", right: "16px", bottom: "80px", zIndex: 99999, background: "#fff",
        border: "2px solid #ee4d2d", borderRadius: "8px", padding: "10px", width: "240px",
        maxHeight: "70vh", overflowY: "auto",
        font: "13px sans-serif", color: "#333", boxShadow: "0 2px 10px rgba(0,0,0,.2)",
    };
    const buttonStyle = {
        display: "block", width: "100%", boxSizing: "border-box", margin: "4px 0", padding: "8px",
        background: "#ee4d2d", color: "#fff", border: "none", borderRadius: "6px",
        cursor: "pointer", textAlign: "center", fontSize: "13px",
    };

    // ---- Shopee の検索結果ページ: 商品ごとに価格のコピーボタンを付ける（ページを読むだけ） ----
    // 似た名前の別商品が多いので、集計はせず、同じ商品だと目で確かめたものを選んでもらう
    function itemPrice(link) {
        const prices = [...link.innerText.matchAll(PRICE_RE)].map(m => parseFloat(m[1].replace(/,/g, "")));
        // 値下げ前の価格も並ぶことがあるので、一番安い数字を売値とみなす
        return prices.length ? Math.min(...prices) : null;
    }

    async function copyPrice(price, button) {
        const text = price.toFixed(2);
        try {
            await navigator.clipboard.writeText(text);
        } catch (e) {
            const tmp = document.createElement("textarea");
            tmp.value = text;
            document.body.appendChild(tmp);
            tmp.select();
            document.execCommand("copy");
            tmp.remove();
        }
        // Seller Centre の画面にも「この価格を入れる」ボタンを出す
        GM_setValue(MARKET_KEY, { price, label: "コピーした価格", at: Date.now() });
        button.textContent = "✅ コピーしました";
        setTimeout(() => { button.textContent = `📋 $${text}`; }, 1500);
    }

    function addCopyButtons() {
        // 商品ページへのリンクは「-i.店舗ID.商品ID」の形
        for (const link of document.querySelectorAll('a[href*="-i."]')) {
            if (link.dataset.flsCopy) continue;
            const price = itemPrice(link);
            if (price === null) continue;
            link.dataset.flsCopy = "1";
            const button = document.createElement("button");
            button.type = "button";
            button.textContent = `📋 $${price.toFixed(2)}`;
            Object.assign(button.style, {
                position: "absolute", top: "6px", left: "6px", zIndex: 10, padding: "4px 8px",
                background: "#2673dd", color: "#fff", border: "none", borderRadius: "6px",
                font: "bold 12px sans-serif", cursor: "pointer", boxShadow: "0 1px 4px rgba(0,0,0,.3)",
            });
            // 商品ページへ移動しないよう、クリックをリンクに伝えない
            button.addEventListener("click", event => {
                event.preventDefault();
                event.stopPropagation();
                copyPrice(price, button);
            });
            if (getComputedStyle(link).position === "static") link.style.position = "relative";
            link.appendChild(button);
        }
    }

    function runMarketPage() {
        // 検索結果は下へスクロールするたびに読み込まれるので、新しく出た商品にもボタンを付ける
        addCopyButtons();
        setInterval(addCopyButtons, 1500);
    }

    if (!location.hostname.startsWith("seller.")) {
        runMarketPage();
        return;
    }

    // このスクリプトを配ったスキャナーのサーバー（配るときにサーバーが書き込む）
    const SCANNER_URL = "__SCANNER_URL__";
    const CATEGORY = ["Hobbies & Collections", "Collectible Items", "Anime & Manga Collectibles"];
    // Shopee の画像上限は 2MB。ぎりぎりだと弾かれることがあるので少し余裕を持たせる
    const PHOTO_MAX_BYTES = 1.9 * 1024 * 1024;
    const PHOTO_SIDES = [1600, 1280, 1000];
    const PHOTO_QUALITIES = [0.9, 0.82, 0.74, 0.66];
    const MAX_IMAGES = 9;

    const sleep = ms => new Promise(r => setTimeout(r, ms));
    const setInputValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;

    async function waitFor(find, timeout = 8000) {
        const start = Date.now();
        while (Date.now() - start < timeout) {
            const found = find();
            if (found) return found;
            await sleep(200);
        }
        return null;
    }

    // 各入力欄は「.edit-row」の中にあり、先頭の行が項目名になっている
    function rowLabel(row) {
        return (row.innerText.split("\n").map(s => s.trim()).filter(s => s && s !== "*")[0] || "");
    }

    function rowOf(label) {
        return [...document.querySelectorAll(".edit-row")].find(r => rowLabel(r) === label);
    }

    function fill(input, value) {
        if (!input || value === undefined || value === null || value === "") return false;
        input.focus();
        setInputValue.call(input, String(value));
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.dispatchEvent(new Event("change", { bubbles: true }));
        input.dispatchEvent(new Event("blur", { bubbles: true }));
        return true;
    }

    function visiblePopover() {
        return [...document.querySelectorAll(".eds-popper.eds-select-popover-content")].find(p => p.offsetParent);
    }

    async function chooseOption(label, value) {
        const row = rowOf(label);
        const selector = row && row.querySelector(".eds-selector");
        if (!selector || !value) return false;
        if (row.innerText.includes(value)) return true;
        selector.click();
        const pop = await waitFor(visiblePopover, 3000);
        if (!pop) return false;
        const search = pop.querySelector("input");
        if (search) {
            fill(search, value);
            await sleep(1500);
        }
        const option = [...pop.querySelectorAll(".eds-option")]
            .find(o => o.innerText.trim().toLowerCase() === value.toLowerCase());
        if (!option) {
            document.body.click();
            return false;
        }
        option.click();
        // 一覧が閉じきる前に次の項目を開くと、前の一覧を拾ってしまうので待つ
        await waitFor(() => !pop.offsetParent, 3000);
        await sleep(300);
        return true;
    }

    async function setCategory() {
        const row = rowOf("Category");
        if (!row) return false;
        if (row.innerText.includes(CATEGORY[CATEGORY.length - 1])) return true;
        const trigger = [...row.querySelectorAll("*")].reverse()
            .find(e => e.children.length === 0 && /Please set category|>/.test(e.innerText)) || row;
        trigger.click();
        const modal = await waitFor(() =>
            [...document.querySelectorAll(".eds-modal__box")].find(m => /Edit Category/.test(m.innerText)), 4000);
        if (!modal) return false;
        for (const name of CATEGORY) {
            const item = await waitFor(() => [...modal.querySelectorAll(".category-item")]
                .filter(e => e.innerText.trim().split("\n")[0].trim() === name).pop(), 4000);
            if (!item) return false;
            item.click();
            await sleep(600);
        }
        const confirm = [...modal.querySelectorAll("button")].find(b => b.innerText.trim() === "Confirm");
        if (!confirm) return false;
        confirm.click();
        // カテゴリを決めると、説明文や価格などの欄が現れる
        return !!(await waitFor(() => rowOf("Price"), 8000));
    }

    function setDescription(text) {
        const row = rowOf("Product Description");
        const editor = row && row.querySelector(".ql-editor");
        if (!editor || !text) return false;
        editor.focus();
        getSelection().selectAllChildren(editor);
        // 入力欄が独自のエディタなので、値を直接入れず「文字を打った」扱いにする
        return document.execCommand("insertText", false, text);
    }

    async function enableShipping() {
        const row = rowOf("Shipping Fee");
        if (!row) return 0;
        let count = 0;
        for (const sw of row.querySelectorAll(".eds-switch.eds-switch--close")) {
            sw.click();
            count++;
            await sleep(400);
        }
        return count;
    }

    async function applyListing(data) {
        const done = [], skipped = [];
        const mark = (ok, name) => (ok ? done : skipped).push(name);

        mark(fill(document.querySelector('input[placeholder^="Brand Name + Product Type"]'), data.name), "商品名");
        mark(await setCategory(), "カテゴリ");
        await sleep(800);
        mark(await chooseOption("Brand", data.brand), `ブランド(${data.brand || "-"})`);
        mark(await chooseOption("Region of Origin", "Japan"), "原産地");
        mark(setDescription(data.description), "説明文");
        mark(fill(rowOf("Price") && rowOf("Price").querySelector("input"), data.price), "価格");
        mark(fill(rowOf("Stock") && rowOf("Stock").querySelector("input"), data.stock), "在庫");
        mark(fill(rowOf("Weight") && rowOf("Weight").querySelector("input"), data.weight), "重さ");
        const parcel = rowOf("Parcel Size") ? rowOf("Parcel Size").querySelectorAll("input") : [];
        const size = data.parcel || [];
        mark(parcel.length === 3 && size.length === 3 &&
            [0, 1, 2].every(i => fill(parcel[i], size[i])), "梱包サイズ");
        await sleep(800);
        // 重さを入れてから配送方法をオンにする（重さが無いと選べない配送方法がある）
        await enableShipping();
        mark(fill(rowOf("Parent SKU") && rowOf("Parent SKU").querySelector("input"), data.jan), "SKU(JAN)");
        return { done, skipped };
    }

    // ---- 出品前チェック: 画面の内容を読むだけで、何も書き換えない ----
    const KANA = /[぀-ヿ]/; // ひらがな・カタカナ (U+3040–U+30FF)
    const KANJI = /[一-鿿]/; // 漢字 (U+4E00–U+9FFF)
    // 偽物と疑われて出品停止になりやすい言葉
    const RISKY_WORDS = ["replica", "fake", "copy", "bootleg", "knockoff", "imitation", "unofficial"];

    function rowText(label) {
        const row = rowOf(label);
        return row ? row.innerText : "";
    }

    function inputValue(label) {
        const input = rowOf(label) && rowOf(label).querySelector("input");
        return input ? input.value.trim() : "";
    }

    function checkListing() {
        const results = [];
        const add = (level, label, message) => results.push({ level, label, message });
        // 台湾は繁体字で出品するので、漢字は sg のときだけ日本語の消し忘れとみなす
        const foreign = text => KANA.test(text) || (location.hostname.endsWith(".sg") && KANJI.test(text));

        const imageCount = Number((rowText("Product Images").match(/\((\d+)\/\d+\)/) || [])[1] || 0);
        if (!imageCount) add("ng", "Product Images", "写真がありません");
        else if (imageCount < 3) add("warn", "Product Images", `写真が${imageCount}枚です。箱の正面・背面・横など3枚以上あると売れやすくなります`);

        const name = (document.querySelector('input[placeholder^="Brand Name + Product Type"]') || {}).value || "";
        const lowerName = name.toLowerCase();
        if (!name.trim()) add("ng", "Product Name", "商品名が空です");
        else {
            if (foreign(name)) add("ng", "Product Name", "商品名に日本語が残っています");
            if (name.length < 25) add("warn", "Product Name", "商品名が短いです。作品名・キャラ名・シリーズ名を入れると検索に出やすくなります");
            const risky = RISKY_WORDS.filter(w => new RegExp(`\\b${w}\\b`).test(lowerName));
            if (risky.length) add("ng", "Product Name", `偽物と誤解される言葉があります: ${risky.join(", ")}`);
        }

        const category = rowText("Category");
        if (!category || /Please set category/.test(category)) add("ng", "Category", "カテゴリが未設定です");
        else if (!category.includes(CATEGORY[CATEGORY.length - 1])) add("warn", "Category", `カテゴリがいつもと違います（いつもは ${CATEGORY.join(" > ")}）`);

        if (/Please select|No Brand/i.test(rowText("Brand"))) add("warn", "Brand", "ブランドが未設定です");
        if (rowOf("Region of Origin") && !rowText("Region of Origin").includes("Japan")) add("warn", "Region of Origin", "原産地が Japan になっていません");
        if (rowOf("Condition") && !/\bNew\b/.test(rowText("Condition"))) add("warn", "Condition", "状態が New になっていません");

        const editor = rowOf("Product Description") && rowOf("Product Description").querySelector(".ql-editor");
        const description = editor ? editor.innerText.trim() : "";
        const descJan = (description.match(/\b\d{13}\b/) || [])[0];
        if (!description) add("ng", "Product Description", "説明文が空です");
        else {
            if (foreign(description)) add("ng", "Product Description", "説明文に日本語が残っています");
            if (description.length < 100) add("warn", "Product Description", "説明文が短いです（100文字未満）");
            if (!descJan) add("warn", "Product Description", "説明文に JAN がありません");
        }

        const price = parseFloat(inputValue("Price"));
        if (!(price > 0)) add("ng", "Price", "価格が入っていません");
        const stock = parseInt(inputValue("Stock"), 10);
        if (!(stock > 0)) add("ng", "Stock", "在庫が0です");

        const weight = parseFloat(inputValue("Weight"));
        const shipping = rowText("Shipping Fee");
        const maxWeight = Math.max(0, ...[...shipping.matchAll(/max\s*([\d.]+)\s*kg/gi)].map(m => parseFloat(m[1])));
        if (!(weight > 0)) add("ng", "Weight", "重さが入っていません");
        else if (maxWeight && weight > maxWeight) add("ng", "Weight", `重さ ${weight}kg が配送の上限 ${maxWeight}kg を超えています`);
        else if (weight < 0.15) add("warn", "Weight", `重さ ${weight}kg は軽すぎませんか。箱と梱包材込みの重さにしてください`);

        const parcel = rowOf("Parcel Size") ? [...rowOf("Parcel Size").querySelectorAll("input")].map(i => parseFloat(i.value)) : [];
        if (parcel.length !== 3 || !parcel.every(v => v > 0)) {
            add("warn", "Parcel Size", "梱包サイズが空です。送料が実際とずれることがあります");
        } else if (weight > 0) {
            // 大きくて軽い荷物は、容積から計算した重さで送料がかかることがある
            const volumetric = parcel[0] * parcel[1] * parcel[2] / 6000;
            if (volumetric > weight) add("warn", "Parcel Size", `容積重量 ${volumetric.toFixed(2)}kg が実際の重さ ${weight}kg より重いです。送料が高くなる場合があります`);
        }

        const switches = rowOf("Shipping Fee") ? [...rowOf("Shipping Fee").querySelectorAll(".eds-switch")] : [];
        const open = switches.filter(s => s.classList.contains("eds-switch--open")).length;
        if (!open) add("ng", "Shipping Fee", "配送方法がひとつもオンになっていません");
        else if (open < switches.length) add("warn", "Shipping Fee", `配送方法が ${switches.length - open}つオフです`);

        const sku = inputValue("Parent SKU");
        if (!sku) add("warn", "Parent SKU", "Parent SKU（JAN）が空です。在庫の照合に使います");
        else if (descJan && sku !== descJan) add("warn", "Parent SKU", `Parent SKU ${sku} と説明文の JAN ${descJan} が違います`);

        // Shopee 自身が出している入力エラー
        for (const row of document.querySelectorAll(".edit-row")) {
            const errors = [...row.querySelectorAll('[class*="error"]')]
                .filter(e => e.offsetParent && e.children.length === 0 && e.innerText.trim());
            for (const e of errors) add("ng", rowLabel(row), "Shopee のエラー: " + e.innerText.trim());
        }
        return results;
    }

    // ---- 写真: 2MB 以下に縮小してから、Shopee の画像欄に入れる ----
    async function resizePhoto(file) {
        const url = URL.createObjectURL(file);
        try {
            const img = new Image();
            img.src = url;
            await img.decode();
            const srcW = img.naturalWidth, srcH = img.naturalHeight;
            for (const side of PHOTO_SIDES) {
                const scale = Math.min(1, side / Math.max(srcW, srcH));
                const w = Math.round(srcW * scale), h = Math.round(srcH * scale);
                const canvas = document.createElement("canvas");
                // Shopee は 1:1 の画像が必要なので、白い余白を足して正方形にする
                canvas.width = canvas.height = Math.max(w, h);
                const ctx = canvas.getContext("2d");
                ctx.fillStyle = "#fff";
                ctx.fillRect(0, 0, canvas.width, canvas.height);
                ctx.drawImage(img, (canvas.width - w) / 2, (canvas.height - h) / 2, w, h);
                for (const q of PHOTO_QUALITIES) {
                    const blob = await new Promise(r => canvas.toBlob(r, "image/jpeg", q));
                    if (blob && blob.size <= PHOTO_MAX_BYTES) {
                        return new File([blob], file.name.replace(/\.[^.]+$/, "") + ".jpg", { type: "image/jpeg" });
                    }
                }
            }
            throw new Error("2MB 以下にできませんでした");
        } finally {
            URL.revokeObjectURL(url);
        }
    }

    async function addPhotos(files) {
        const row = [...document.querySelectorAll(".edit-row")].find(r => rowLabel(r) === "Product Images");
        const input = row && row.querySelector('input[type="file"]');
        if (!input) throw new Error("画像の欄が見つかりません");
        const dt = new DataTransfer();
        for (const file of [...files].slice(0, MAX_IMAGES)) {
            dt.items.add(await resizePhoto(file));
        }
        input.files = dt.files;
        input.dispatchEvent(new Event("change", { bubbles: true }));
        return dt.files.length;
    }

    // Seller Centre の画面からは他のサイトへ通信できないため、Tampermonkey の機能で通信する
    function api(method, path, body) {
        return new Promise((resolve, reject) => {
            GM_xmlhttpRequest({
                method,
                url: SCANNER_URL + path,
                headers: { "Content-Type": "application/json" },
                data: body ? JSON.stringify(body) : undefined,
                timeout: 60000,
                onload: res => {
                    let data = {};
                    try { data = JSON.parse(res.responseText); } catch (e) {}
                    if (res.status >= 200 && res.status < 300) resolve(data);
                    else reject(new Error(data.detail || `HTTP ${res.status}`));
                },
                onerror: () => reject(new Error("スキャナーに接続できませんでした")),
                // 無料のサーバーは止まっていると起きるまで1分ほどかかる
                ontimeout: () => reject(new Error("スキャナーの応答がありません。少し待ってからもう一度押してください")),
            });
        });
    }

    function toListing(item) {
        return { ...item, parcel: [item.w, item.l, item.h] };
    }

    // ---- 画面の右下に操作パネルを出す ----
    function buildPanel() {
        const panel = document.createElement("div");
        panel.id = "fls-panel";
        panel.innerHTML = `
            <div style="font-weight:bold;margin-bottom:6px">出品名スキャナー</div>
            <button id="fls-load">📥 出品待ちを表示</button>
            <div id="fls-list"></div>
            <label id="fls-photo-label">🖼️ 写真を縮小して追加
                <input id="fls-photo" type="file" accept="image/*" multiple hidden>
            </label>
            <button id="fls-market">💹 Shopeeで相場を調べる</button>
            <div id="fls-market-result"></div>
            <button id="fls-check">🔍 出品前チェック</button>
            <div id="fls-check-result"></div>
            <div id="fls-status"></div>`;
        Object.assign(panel.style, panelStyle);
        for (const el of [panel.querySelector("#fls-load"), panel.querySelector("#fls-photo-label"),
            panel.querySelector("#fls-market")]) {
            Object.assign(el.style, buttonStyle);
        }

        // 商品名から検索語を作り、同じ国の Shopee を安い順で開く
        panel.querySelector("#fls-market").addEventListener("click", () => {
            const name = (document.querySelector('input[placeholder^="Brand Name + Product Type"]') || {}).value || "";
            const words = name.replace(MARKET_NOISE, " ").replace(/["“”:：()（）~〜]/g, " ").split(/\s+/).filter(Boolean);
            if (!words.length) {
                status.textContent = "先に商品名を入れてください。";
                return;
            }
            const site = location.hostname.replace(/^seller\./, "");
            const keyword = words.slice(0, MARKET_WORDS).join(" ");
            window.open(`https://${site}/search?keyword=${encodeURIComponent(keyword)}&sortBy=price&order=asc`, "_blank");
            status.textContent = "開いたページで同じ商品を探し、左上の「📋 $価格」を押すとコピーされ、ここに入れるボタンが出ます。";
        });

        // 相場のページで選んだ価格を受け取って、価格欄に入れるボタンを出す
        const marketResult = panel.querySelector("#fls-market-result");
        function showMarketPrice(data) {
            marketResult.innerHTML = "";
            if (!data || !data.price) return;
            const b = document.createElement("button");
            b.textContent = `相場 $${data.price.toFixed(2)}（${data.label}）を価格に入れる`;
            Object.assign(b.style, buttonStyle, { background: "#2673dd", fontSize: "12px" });
            b.addEventListener("click", () => {
                const input = rowOf("Price") && rowOf("Price").querySelector("input");
                status.textContent = fill(input, data.price.toFixed(2))
                    ? "価格を入れました。利益が出るか確かめてください。"
                    : "価格の欄が見つかりません（カテゴリを先に決めてください）。";
            });
            marketResult.appendChild(b);
        }
        GM_addValueChangeListener(MARKET_KEY, (key, oldValue, newValue) => showMarketPrice(newValue));
        Object.assign(panel.querySelector("#fls-check").style, buttonStyle, { background: "#2673dd" });
        const checkResult = panel.querySelector("#fls-check-result");

        function runCheck() {
            const results = checkListing();
            checkResult.innerHTML = "";
            const ng = results.filter(r => r.level === "ng").length;
            const summary = document.createElement("div");
            summary.style.fontWeight = "bold";
            summary.style.margin = "4px 0";
            summary.textContent = ng ? `❌ 直すところが${ng}件あります`
                : results.length ? "⚠️ 公開はできます。気になる点を確認してください" : "✅ 問題なし。公開して大丈夫です";
            checkResult.appendChild(summary);
            for (const r of results) {
                const line = document.createElement("div");
                line.textContent = `${r.level === "ng" ? "❌" : "⚠️"} ${r.message}`;
                Object.assign(line.style, { fontSize: "12px", margin: "3px 0", cursor: "pointer", textDecoration: "underline dotted" });
                line.title = "クリックでその欄へ移動";
                // 押すとその欄まで画面を動かす
                line.addEventListener("click", () => {
                    const row = rowOf(r.label);
                    if (row) row.scrollIntoView({ behavior: "smooth", block: "center" });
                });
                checkResult.appendChild(line);
            }
        }
        panel.querySelector("#fls-check").addEventListener("click", runCheck);
        const status = panel.querySelector("#fls-status");
        Object.assign(status.style, { marginTop: "6px", fontSize: "12px", whiteSpace: "pre-wrap" });
        const list = panel.querySelector("#fls-list");

        async function showDoneButton(item) {
            const done = document.createElement("button");
            done.textContent = "✅ 出品できたので一覧から消す";
            Object.assign(done.style, buttonStyle, { background: "#28a745" });
            done.addEventListener("click", async () => {
                try {
                    await api("POST", "/api/queue/done", { id: item.id });
                    done.remove();
                    status.textContent = "出品待ちから消しました。";
                } catch (e) {
                    status.textContent = "消せませんでした: " + e.message;
                }
            });
            status.after(done);
        }

        async function loadQueue() {
            status.textContent = "出品待ちを読み込んでいます...";
            list.innerHTML = "";
            try {
                const data = await api("GET", "/api/queue");
                status.textContent = data.items.length
                    ? "入力する商品を選んでください。"
                    : "出品待ちはありません。スマホのスキャナーで「PCの出品待ちに送る」を押してください。";
                for (const item of data.items) {
                    const row = document.createElement("button");
                    row.textContent = `${item.name.slice(0, 60)}  $${item.price || "-"}`;
                    Object.assign(row.style, {
                        display: "block", width: "100%", textAlign: "left", margin: "4px 0", padding: "6px",
                        background: "#fff7f5", border: "1px solid #f3c1b5", borderRadius: "6px",
                        cursor: "pointer", fontSize: "12px",
                    });
                    row.addEventListener("click", async () => {
                        status.textContent = "入力しています...";
                        try {
                            const { done, skipped } = await applyListing(toListing(item));
                            status.textContent = `入力: ${done.join("、")}` +
                                (skipped.length ? `\n手で入れてください: ${skipped.join("、")}` : "") +
                                "\n内容を確認してから公開してください。";
                            showDoneButton(item);
                        } catch (e) {
                            status.textContent = "入力できませんでした: " + e.message;
                        }
                    });
                    list.appendChild(row);
                }
            } catch (e) {
                status.textContent = "読み込めませんでした: " + e.message;
            }
        }

        panel.querySelector("#fls-load").addEventListener("click", loadQueue);
        panel.querySelector("#fls-photo").addEventListener("change", async (event) => {
            const files = event.target.files;
            if (!files.length) return;
            status.textContent = `写真を縮小しています... (${files.length}枚)`;
            try {
                const count = await addPhotos(files);
                status.textContent = `写真を${count}枚追加しました。`;
            } catch (e) {
                status.textContent = "写真を追加できませんでした: " + e.message;
            }
            event.target.value = "";
        });
        document.body.appendChild(panel);
    }

    // Seller Centre は画面遷移でページを読み直さないので、商品の編集画面かどうかを見張る
    setInterval(() => {
        const onForm = !!document.querySelector('input[placeholder^="Brand Name + Product Type"]');
        const panel = document.getElementById("fls-panel");
        if (onForm && !panel) buildPanel();
        if (!onForm && panel) panel.remove();
    }, 1000);
})();
