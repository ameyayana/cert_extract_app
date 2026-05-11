const API = "";

let currentFile = null;
let isServiceUpload = false;

let rawData = [];
let currentCollectionName = "";
let batchResults = [];

/* =========================
   THEME
========================= */

function initTheme() {
  if (
    localStorage.theme === "dark" ||
    (!("theme" in localStorage) &&
      window.matchMedia("(prefers-color-scheme: dark)").matches)
  ) {
    document.documentElement.classList.add("dark");
    updateThemeIcon(true);
  } else {
    document.documentElement.classList.remove("dark");
    updateThemeIcon(false);
  }
}

function toggleTheme() {
  if (document.documentElement.classList.contains("dark")) {
    document.documentElement.classList.remove("dark");
    localStorage.theme = "light";
    updateThemeIcon(false);
  } else {
    document.documentElement.classList.add("dark");
    localStorage.theme = "dark";
    updateThemeIcon(true);
  }
}

function updateThemeIcon(isDark) {
  const icon = document.getElementById("themeIcon");

  if (isDark) {
    icon.classList.remove("fa-moon");
    icon.classList.add("fa-sun");
  } else {
    icon.classList.remove("fa-sun");
    icon.classList.add("fa-moon");
  }
}

/* =========================
   TABS
========================= */

function switchTab(tab) {
  document
    .getElementById("tab-dashboard")
    .classList.toggle("hidden", tab !== "dashboard");

  document
    .getElementById("tab-upload")
    .classList.toggle("hidden", tab !== "upload");
}

/* =========================
   DRAG DROP
========================= */

function initDragAndDrop() {
  const dropZone = document.getElementById("dropZone");

  ["dragenter", "dragover", "dragleave", "drop"].forEach((eventName) => {
    dropZone.addEventListener(eventName, preventDefaults, false);
  });

  function preventDefaults(e) {
    e.preventDefault();
    e.stopPropagation();
  }

  dropZone.addEventListener("drop", handleDrop, false);

  function handleDrop(e) {
    const dt = e.dataTransfer;
    const files = dt.files;

    if (files.length > 0) {
      document.getElementById("fileInput").files = files;
      handleFileSelect();
    }
  }
}

/* =========================
   LOAD COLLECTIONS
========================= */

async function init() {
  try {
    const res = await axios.get(`${API}/api/collections`);

    const nav = document.getElementById("collectionList");

    nav.innerHTML = res.data.collections
      .map(
        (c) => `
        <button
          onclick="loadCollection('${c}')"
          class="w-full text-left px-3 py-2 rounded text-gray-600 dark:text-gray-300 hover:bg-blue-50 dark:hover:bg-blue-900 text-sm transition"
        >
          ${c}
        </button>
      `
      )
      .join("");
  } catch (e) {
    console.error(e);
  }
}

async function loadCollection(col) {
  currentCollectionName = col;

  try {
    const res = await axios.get(`${API}/api/collection/${col}`);

    rawData = res.data.data;

    renderTable(rawData);
  } catch (e) {
    console.error(e);
  }
}

/* =========================
   TABLE
========================= */

function renderTable(data) {
  const tbody = document.getElementById("tableBody");

  document.getElementById(
    "countBadge"
  ).innerText = `${data.length} items`;

  tbody.innerHTML = data
    .map(
      (item) => `
      <tr class="hover:bg-gray-50 dark:hover:bg-gray-700 transition">

        <td class="px-6 py-3 font-medium dark:text-white">
          ${item.serial}
        </td>

        <td class="px-6 py-3 dark:text-gray-300">
          ${item.model}
        </td>

        <td class="px-6 py-3">
          ${item.expiry_date}
        </td>

        <td class="px-6 py-3">
          ${item.last_updated || "-"}
        </td>

        <td class="px-6 py-3 text-right">
          <button
            onclick="deleteItem('${currentCollectionName}','${item.id}')"
            class="text-red-500"
          >
            Delete
          </button>
        </td>

      </tr>
    `
    )
    .join("");
}

/* =========================
   FILE UPLOAD
========================= */

async function handleFileSelect() {
  const files = document.getElementById("fileInput").files;

  if (!files.length) return;

  document.getElementById("loading").classList.remove("hidden");

  try {
    for (let file of files) {
      const fd = new FormData();

      fd.append("file", file);

      await axios.post(`${API}/extract`, fd);
    }

    alert("Upload complete");
  } catch (e) {
    console.error(e);
    alert("Upload failed");
  }

  document.getElementById("loading").classList.add("hidden");
}

/* =========================
   SAVE
========================= */

function saveManual() {
  alert("Manual save triggered");
}

/* =========================
   DELETE
========================= */

async function deleteItem(col, id) {
  if (!confirm("Delete this record permanently?")) return;

  await axios.delete(`${API}/api/collection/${col}/${id}`);

  loadCollection(col);
}

/* =========================
   PREVIEW
========================= */

function updatePreview() {
  console.log("Preview updating...");
}

/* =========================
   START
========================= */

document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  initDragAndDrop();
  init();
});
