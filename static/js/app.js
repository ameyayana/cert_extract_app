// --- Configuration ---
const API = ""; // If your backend is on a different port, put it here (e.g., "http://localhost:5000")
let currentFile = null;
let isServiceUpload = false;
let rawData = []; 
let currentCollectionName = "";
let batchResults = []; 

// --- Dark Mode Logic ---
function initTheme() {
    if (localStorage.theme === 'dark' || (!('theme' in localStorage) && window.matchMedia('(prefers-color-scheme: dark)').matches)) {
        document.documentElement.classList.add('dark');
        updateThemeIcon(true);
    } else {
        document.documentElement.classList.remove('dark');
        updateThemeIcon(false);
    }
}

function toggleTheme() {
    if (document.documentElement.classList.contains('dark')) {
        document.documentElement.classList.remove('dark');
        localStorage.theme = 'light';
        updateThemeIcon(false);
    } else {
        document.documentElement.classList.add('dark');
        localStorage.theme = 'dark';
        updateThemeIcon(true);
    }
}

function updateThemeIcon(isDark) {
    const icon = document.getElementById('themeIcon');
    if (icon) {
        if (isDark) {
            icon.classList.remove('fa-moon');
            icon.classList.add('fa-sun');
        } else {
            icon.classList.remove('fa-sun');
            icon.classList.add('fa-moon');
        }
    }
}

// --- Navigation & Tabs ---
function switchTab(tab) {
    document.getElementById("tab-dashboard").classList.toggle("hidden", tab !== "dashboard");
    document.getElementById("tab-upload").classList.toggle("hidden", tab !== "upload");

    const btnDash = document.getElementById("btn-dashboard");
    const btnUp = document.getElementById("btn-upload");

    if (tab === "dashboard") {
        btnDash.className = "px-4 py-2 font-medium text-blue-600 border-b-2 border-blue-600 transition dark:text-blue-400 dark:border-blue-400";
        btnUp.className = "px-4 py-2 font-medium text-gray-500 hover:text-blue-600 transition dark:text-gray-400 dark:hover:text-blue-400";
    } else {
        btnDash.className = "px-4 py-2 font-medium text-gray-500 hover:text-blue-600 transition dark:text-gray-400 dark:hover:text-blue-400";
        btnUp.className = "px-4 py-2 font-medium text-blue-600 border-b-2 border-blue-600 transition dark:text-blue-400 dark:border-blue-400";
    }
}

// --- Folder & Data Loading ---
async function init() {
    try {
        const res = await axios.get(`${API}/api/collections`);
        const nav = document.getElementById("collectionList");
        if (!nav) return;

        nav.innerHTML = res.data.collections
            .map(c => `
                <button onclick="loadCollection('${c}')" class="w-full text-left px-3 py-2 rounded text-gray-600 dark:text-gray-300 hover:bg-blue-50 dark:hover:bg-blue-900 hover:text-blue-600 dark:hover:text-blue-200 text-sm transition flex items-center">
                    <i class="fas ${c.includes("SERVICE") ? "fa-tools text-purple-400" : "fa-folder text-yellow-400"} mr-2"></i> 
                    ${c.replace("_SERVICE", " (Client)")}
                </button>`).join("");
    } catch (e) {
        console.error("Critical Error: Could not load folders.", e);
    }
}

async function loadCollection(col) {
    currentCollectionName = col;
    document.getElementById("tableTitle").innerText = col.replace("_SERVICE", " (Service)");
    document.getElementById("tableBody").innerHTML = `<tr><td colspan="5" class="p-10 text-center dark:text-gray-300"><i class="fas fa-spinner fa-spin"></i> Loading...</td></tr>`;
    try {
        const res = await axios.get(`${API}/api/collection/${col}`);
        rawData = res.data.data;
        populateYearFilter();
        applyFilters();
    } catch (e) {
        console.error(e);
    }
}

// --- Filtering & Table Rendering ---
function populateYearFilter() {
    const dateType = document.getElementById("filterDateType").value;
    const yearSelect = document.getElementById("filterYear");
    const currentVal = yearSelect.value;
    yearSelect.innerHTML = '<option value="all">All</option>';
    const years = new Set();

    rawData.forEach(item => {
        let y = null;
        if (dateType === "expiry") {
            const dateStr = item.expiry_date || item.exp;
            if (dateStr) y = new Date(dateStr).getFullYear();
        } else if (dateType === "cert") {
            const certStr = item.cert || "";
            const match = certStr.match(/20\d{2}/);
            if (match) y = parseInt(match[0]);
        }
        if (y && !isNaN(y) && y > 2000 && y < 2100) years.add(y);
    });

    Array.from(years).sort((a, b) => a - b).forEach(y => {
        const opt = document.createElement("option");
        opt.value = y; opt.innerText = y;
        if (String(y) === currentVal) opt.selected = true;
        yearSelect.appendChild(opt);
    });
}

function applyFilters() {
    const query = document.getElementById("searchBox").value.toLowerCase();
    const field = document.getElementById("filterField").value;
    const sort = document.getElementById("sortOrder").value;
    const yearFilter = document.getElementById("filterYear").value;
    const dateType = document.getElementById("filterDateType").value;

    let filtered = rawData.filter((item) => {
        let matchesSearch = !query || (field === "all" ?
            Object.values(item).some(val => String(val).toLowerCase().includes(query)) :
            String(item[field] || "").toLowerCase().includes(query));

        let matchesYear = true;
        if (yearFilter !== "all") {
            if (dateType === "expiry") {
                const d = item.expiry_date || item.exp;
                if (!d || !d.startsWith(yearFilter)) matchesYear = false;
            } else if (dateType === "cert") {
                const c = item.cert || "";
                if (!c.includes(yearFilter)) matchesYear = false;
            }
        }
        return matchesSearch && matchesYear;
    });

    filtered.sort((a, b) => {
        const dateA = new Date(a.last_updated || 0);
        const dateB = new Date(b.last_updated || 0);
        if (sort === "updated_desc") return dateB - dateA;
        if (sort === "updated_asc") return dateA - dateB;
        if (sort === "exp_asc") return new Date(a.expiry_date || "2099") - new Date(b.expiry_date || "2099");
        return 0;
    });

    renderTable(filtered);
}

function renderTable(data) {
    const tbody = document.getElementById("tableBody");
    document.getElementById("countBadge").innerText = `${data.length} items`;
    if (data.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" class="p-10 text-center text-gray-400 dark:text-gray-500">No records found</td></tr>`;
        return;
    }
    tbody.innerHTML = data.map((item) => {
        const safeItem = JSON.stringify(item).replace(/"/g, "&quot;");
        return `
            <tr class="hover:bg-gray-50 dark:hover:bg-gray-700 transition border-b border-gray-100 dark:border-gray-700 cursor-pointer" onclick="showDetails(${safeItem})">
                <td class="px-6 py-3 font-medium text-gray-900 dark:text-white">${item.serial}</td>
                <td class="px-6 py-3 text-gray-600 dark:text-gray-300">${item.model}</td>
                <td class="px-6 py-3"><span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200">${item.expiry_date || item.exp}</span></td>
                <td class="px-6 py-3 text-xs text-gray-500 dark:text-gray-400">${item.last_updated ? new Date(item.last_updated).toLocaleDateString() : '-'}</td>
                <td class="px-6 py-3 text-right space-x-3" onclick="event.stopPropagation()">
                    ${item.pdf_url ? `<a href="${item.pdf_url}" target="_blank" class="text-blue-500"><i class="fas fa-file-pdf"></i></a>` : ""}
                    <button onclick="deleteItem('${currentCollectionName}', '${item.id}')" class="text-red-400"><i class="fas fa-trash"></i></button>
                </td>
            </tr>`;
    }).join("");
}

// --- Modals & Actions ---
function showDetails(item) {
    document.getElementById("detSerial").value = item.serial;
    document.getElementById("detModel").value = item.model;
    document.getElementById("detCal").value = item.calibration_date || item.cal || "";
    document.getElementById("detExp").value = item.expiry_date || item.exp || "";
    document.getElementById("detCert").value = item.cert || "";
    document.getElementById("detLot").value = item.lot || "";
    document.getElementById("detailModal").classList.remove("hidden");
}

function closeModal(id) { document.getElementById(id).classList.add("hidden"); }

function copyText(id) {
    const text = document.getElementById(id).innerText;
    navigator.clipboard.writeText(text);
    alert("Copied!");
}

// --- Upload Logic ---
async function handleFileSelect() {
    const files = document.getElementById("fileInput").files;
    if (!files.length) return;
    document.getElementById("loading").classList.remove("hidden");
    // ... (Your extraction logic here)
    document.getElementById("loading").classList.add("hidden");
}

// --- CRITICAL: Initialization Wrapper ---
// This ensures that even if the script is in a separate file, 
// it waits for the webpage elements to exist before running.
document.addEventListener('DOMContentLoaded', () => {
    initTheme();
    // Start loading the folders immediately
    init(); 
    
    // Setup Drag and Drop if on the upload tab
    const dropZone = document.getElementById('dropZone');
    if(dropZone) {
        dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('bg-blue-50'); });
        dropZone.addEventListener('dragleave', () => { dropZone.classList.remove('bg-blue-50'); });
        dropZone.addEventListener('drop', (e) => {
            e.preventDefault();
            document.getElementById('fileInput').files = e.dataTransfer.files;
            handleFileSelect();
        });
    }
});
