// ==UserScript==
// @name         Shopee 出品の自動入力（出品名スキャナー）
// @namespace    https://github.com/test90654/figure-listing-scanner
// @version      1.0.0
// @description  出品名スキャナーでコピーした商品データを、Shopee Seller Centre の商品追加画面に入力する
// @match        https://seller.shopee.sg/portal/product/*
// @match        https://seller.shopee.tw/portal/product/*
// @match        https://seller.shopee.co.th/portal/product/*
// @grant        none
// ==/UserScript==

// 入力するだけで、保存や公開のボタンは押さない。内容を確かめてから自分で公開する。
(function () {
    "use strict";

    const DATA_TYPE = "figure-listing-scanner";
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

    async function readListingData() {
        let text = "";
        try {
            text = await navigator.clipboard.readText();
        } catch (e) {
            // クリップボードの読み取りが許可されていないときは、手で貼ってもらう
        }
        if (!text.includes(DATA_TYPE)) {
            text = prompt("出品名スキャナーの「Shopee用にまとめてコピー」で取ったデータを貼り付けてください") || "";
        }
        const data = JSON.parse(text);
        if (data.type !== DATA_TYPE) throw new Error("出品名スキャナーのデータではありません");
        return data;
    }

    // ---- 画面の右下に操作パネルを出す ----
    function buildPanel() {
        const panel = document.createElement("div");
        panel.id = "fls-panel";
        panel.innerHTML = `
            <div style="font-weight:bold;margin-bottom:6px">出品名スキャナー</div>
            <button id="fls-fill">📋 コピーした商品を入力</button>
            <label id="fls-photo-label">🖼️ 写真を縮小して追加
                <input id="fls-photo" type="file" accept="image/*" multiple hidden>
            </label>
            <div id="fls-status"></div>`;
        Object.assign(panel.style, {
            position: "fixed", right: "16px", bottom: "80px", zIndex: 99999, background: "#fff",
            border: "2px solid #ee4d2d", borderRadius: "8px", padding: "10px", width: "220px",
            font: "13px sans-serif", color: "#333", boxShadow: "0 2px 10px rgba(0,0,0,.2)",
        });
        for (const el of [panel.querySelector("#fls-fill"), panel.querySelector("#fls-photo-label")]) {
            Object.assign(el.style, {
                display: "block", width: "100%", boxSizing: "border-box", margin: "4px 0", padding: "8px",
                background: "#ee4d2d", color: "#fff", border: "none", borderRadius: "6px",
                cursor: "pointer", textAlign: "center", fontSize: "13px",
            });
        }
        const status = panel.querySelector("#fls-status");
        Object.assign(status.style, { marginTop: "6px", fontSize: "12px", whiteSpace: "pre-wrap" });

        panel.querySelector("#fls-fill").addEventListener("click", async () => {
            status.textContent = "入力しています...";
            try {
                const data = await readListingData();
                const { done, skipped } = await applyListing(data);
                status.textContent = `入力: ${done.join("、")}` +
                    (skipped.length ? `\n手で入れてください: ${skipped.join("、")}` : "") +
                    "\n内容を確認してから公開してください。";
            } catch (e) {
                status.textContent = "入力できませんでした: " + e.message;
            }
        });
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
