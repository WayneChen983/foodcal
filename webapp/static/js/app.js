const VIEWS = [
  { key: "left_45", title: "左側 45°", step: "步驟 1 / 3", icon: "◐", hint: "從餐點左側約 45° 拍攝，信用卡完整入鏡。" },
  { key: "right_45", title: "右側 45°", step: "步驟 2 / 3", icon: "◑", hint: "從餐點右側約 45° 拍攝，保持相同距離。" },
  { key: "top_90", title: "正上方（俯視 90°）", step: "步驟 3 / 3", icon: "◎", hint: "正上方俯拍，作為分割參考視角；確認信用卡無反光。" },
];

const state = {
  step: 0,
  files: [null, null, null],
  previews: [null, null, null],
};

const $ = (sel) => document.querySelector(sel);

const captureSection = $("#captureSection");
const confirmSection = $("#confirmSection");
const loadingSection = $("#loadingSection");
const resultSection = $("#resultSection");

const preview = $("#preview");
const previewPlaceholder = $("#previewPlaceholder");
const fileInput = $("#fileInput");
const stepLabel = $("#stepLabel");
const viewTitle = $("#viewTitle");
const viewHint = $("#viewHint");
const angleIcon = $("#angleIcon");

function showSection(section) {
  [captureSection, confirmSection, loadingSection, resultSection].forEach((el) => {
    el.classList.toggle("hidden", el !== section);
  });
}

function updateStepUI() {
  const v = VIEWS[state.step];
  stepLabel.textContent = v.step;
  viewTitle.textContent = v.title;
  viewHint.textContent = v.hint;
  angleIcon.textContent = v.icon;

  document.querySelectorAll(".dot").forEach((dot, i) => {
    dot.classList.toggle("active", i === state.step);
    dot.classList.toggle("done", state.files[i] !== null && i !== state.step);
  });

  $("#btnPrev").disabled = state.step === 0;

  const hasFile = state.files[state.step] !== null;
  preview.classList.toggle("hidden", !hasFile);
  previewPlaceholder.classList.toggle("hidden", hasFile);
  $("#btnClear").classList.toggle("hidden", !hasFile);

  if (hasFile && state.previews[state.step]) {
    preview.src = state.previews[state.step];
  } else {
    preview.removeAttribute("src");
  }

  $("#btnNext").textContent = state.step === 2 ? "確認並分析" : "下一步";
}

function setFileForStep(step, file) {
  if (!file) return;
  state.files[step] = file;
  if (state.previews[step]) URL.revokeObjectURL(state.previews[step]);
  state.previews[step] = URL.createObjectURL(file);
  updateStepUI();
}

function clearCurrentStep() {
  const i = state.step;
  state.files[i] = null;
  if (state.previews[i]) URL.revokeObjectURL(state.previews[i]);
  state.previews[i] = null;
  updateStepUI();
}

function allFilesReady() {
  return state.files.every((f) => f !== null);
}

function buildThumbGrid() {
  const grid = $("#thumbGrid");
  grid.innerHTML = "";
  VIEWS.forEach((v, i) => {
    const fig = document.createElement("figure");
    const img = document.createElement("img");
    img.src = state.previews[i];
    img.alt = v.title;
    const cap = document.createElement("figcaption");
    cap.textContent = v.title;
    fig.append(img, cap);
    grid.append(fig);
  });
}

async function analyze() {
  showSection(loadingSection);
  try {
    const cfg = await fetch("/api/config").then((r) => r.json());
    $("#cloudHint").classList.toggle("hidden", cfg.mode !== "remote_proxy");
  } catch {
    /* ignore */
  }

  const form = new FormData();
  VIEWS.forEach((v, i) => form.append(v.key, state.files[i], state.files[i].name || `${v.key}.jpg`));

  try {
    const res = await fetch("/api/analyze", { method: "POST", body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
    renderResults(data);
    showSection(resultSection);
  } catch (err) {
    alert(`分析失敗：${err.message}`);
    showSection(confirmSection);
  }
}

function renderResults(data) {
  $("#demoBanner").classList.toggle("hidden", !data.demo);
  const t = data.totals || {};
  $("#totalKcal").textContent = `${t.calories_kcal ?? "—"} kcal`;
  $("#totalMacros").textContent =
    `蛋白質 ${t.protein_g ?? "—"} g · 脂肪 ${t.fat_g ?? "—"} g · 碳水 ${t.carbohydrates_g ?? "—"} g`;

  const list = $("#itemList");
  list.innerHTML = "";
  (data.items || []).forEach((item) => {
    const li = document.createElement("li");
    const left = document.createElement("div");
    const name = document.createElement("div");
    name.className = "name";
    const isUnknown = item.matched_as === "UNKNOWN";
    name.textContent = isUnknown ? item.original_label : item.matched_as.split(" (")[0];
    if (isUnknown) name.classList.add("unknown");

    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = isUnknown
      ? `體積 ${item.volume_cm3} cm³ · 資料庫無對應`
      : `體積 ${item.volume_cm3} cm³ · ${item.protein_g}P / ${item.fat_g}F / ${item.carbs_g}C g`;

    left.append(name, meta);

    const kcal = document.createElement("div");
    kcal.className = "kcal";
    kcal.textContent = isUnknown ? "—" : `${item.kcal} kcal`;

    li.append(left, kcal);
    list.append(li);
  });
}

function resetAll() {
  state.step = 0;
  state.files = [null, null, null];
  state.previews.forEach((u) => u && URL.revokeObjectURL(u));
  state.previews = [null, null, null];
  updateStepUI();
  showSection(captureSection);
}

async function loadHealth() {
  const badge = $("#statusBadge");
  try {
    const res = await fetch("/api/health");
    const h = await res.json();
    badge.classList.remove("ok", "warn");
    if (!res.ok || h.status === "error") {
      badge.textContent = h.detail || "雲端離線";
      badge.classList.add("warn");
      return;
    }
    if (h.local_proxy && h.remote_api) {
      badge.textContent = h.gpu_available ? "雲端 GPU 連線中" : "雲端連線（無 GPU）";
      badge.classList.add(h.gpu_available ? "ok" : "warn");
    } else if (h.demo_mode) {
      badge.textContent = "示範模式";
      badge.classList.add("warn");
    } else if (h.gpu_available) {
      badge.textContent = "GPU 就緒";
      badge.classList.add("ok");
    } else {
      badge.textContent = "CPU 模式";
      badge.classList.add("warn");
    }
  } catch {
    badge.textContent = "離線";
    badge.classList.add("warn");
  }
}

async function loadFoodDb() {
  const list = $("#foodDbList");
  try {
    const res = await fetch("/api/foods");
    const data = await res.json();
    list.innerHTML = "";
    (data.items || []).forEach((item) => {
      const li = document.createElement("li");
      li.textContent = item.display_name.split(" (")[0];
      list.append(li);
    });
  } catch {
    list.innerHTML = "<li>無法載入資料庫</li>";
  }
}

$("#btnPick").addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => {
  const file = fileInput.files?.[0];
  if (file) setFileForStep(state.step, file);
  fileInput.value = "";
});

$("#btnClear").addEventListener("click", clearCurrentStep);

$("#btnPrev").addEventListener("click", () => {
  if (state.step > 0) {
    state.step -= 1;
    updateStepUI();
  }
});

$("#btnNext").addEventListener("click", () => {
  if (!state.files[state.step]) {
    alert("請先選擇或拍攝此視角的照片");
    return;
  }
  if (state.step < 2) {
    state.step += 1;
    updateStepUI();
  } else if (allFilesReady()) {
    buildThumbGrid();
    showSection(confirmSection);
  }
});

document.querySelectorAll(".dot").forEach((dot) => {
  dot.addEventListener("click", () => {
    state.step = Number(dot.dataset.step);
    updateStepUI();
  });
});

$("#btnAnalyze").addEventListener("click", analyze);
$("#btnBackEdit").addEventListener("click", () => {
  state.step = 2;
  updateStepUI();
  showSection(captureSection);
});
$("#btnNew").addEventListener("click", resetAll);

updateStepUI();
loadHealth();
loadFoodDb();
