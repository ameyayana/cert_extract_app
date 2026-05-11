const API = "";
let currentFile = null;
let isServiceUpload = false;

let rawData = []; // Store for filtering
let currentCollectionName = "";
let batchResults = []; // Store batch results

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
  if(icon) {
    if(isDark) {
        icon.classList.remove('fa-moon');
        icon.classList.add('fa-sun');
    } else {
        icon.classList.remove('fa-sun');
        icon.classList.add('fa-moon');
    }
  }
}

// --- Drag and Drop Logic ---
function initDragAndDrop() {
  const dropZone = document.getElementById('dropZone');
  const fileInput = document.getElementById('fileInput');

  if (!dropZone) return;

  ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
      dropZone.addEventListener(eventName, (e) => {
          e.preventDefault();
          e.stopPropagation();
      }, false);
  });

  function highlight(e) {
      dropZone.classList.add('border-blue-500', 'bg-blue-50', 'dark:bg-gray-600');
      dropZone.classList.remove('border-gray-300', 'dark:border-gray-600');
  }

  function unhighlight(e) {
      dropZone.classList.remove('border-blue-500', 'bg-blue-50', 'dark:bg-gray-600');
      dropZone.classList.add('border-gray-300', 'dark:border-gray-600');
  }

  ['dragenter', 'dragover'].forEach(eventName => {
      dropZone.addEventListener(eventName, highlight, false);
  });

  ['dragleave', 'drop'].forEach(eventName => {
      dropZone.addEventListener(eventName, unhighlight, false);
  });

  dropZone.addEventListener('drop', (e) => {
      const dt = e.dataTransfer;
      const files = dt.files;
      if (files.length > 0) {
          fileInput.files = files;
          handleFileSelect();
      }
  }, false);
}

// --- Navigation Helpers ---
function validateLink(el) {
  const href = el.getAttribute('href');
  if (!href || href === '#' || href === 'null') {
      alert("File/Link not available for this record.");
      return false;
  }
  return true;
}

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

function setUploadType(isService) {
  isServiceUpload = isService;
  const r = document.getElementById("type-rental");
  const s = document.getElementById("type-service");
  if (isService) {
    r.className = "flex-1 py-2 rounded-l-md text-sm font-bold bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-300 hover:text-gray-800 dark:hover:text-white transition";
    s.className = "flex-1 py-2 rounded-r-md text-sm font-bold bg-purple-600 text-white transition";
  } else {
    r.className = "flex-1 py-2 rounded-l-md text-sm font-bold bg-blue-600 text-white transition";
    s.className = "flex-1 py-2 rounded-r-md text-sm font-bold bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-300 hover:text-gray-800 dark:hover:text-white transition";
  }
}

// --- API & Data Logic ---
async function init() {
  try {
    const res = await axios.get(`${API}/api/collections`);
    const nav = document.getElementById("collectionList");
    nav.innerHTML = res.data.collections
      .map(c => `
        <button onclick="loadCollection('${c}')" class="w-full text-left px-3 py-2 rounded text-gray-600 dark:text-gray-300 hover:bg-blue-50 dark:hover:bg-blue-900 hover:text-blue-600 dark:hover:text-blue-200 text-sm transition flex items-center">
            <i class="fas ${c.includes("SERVICE") ? "fa-tools text-purple-400" : "fa-folder text-yellow-400"} mr-2"></i> 
            ${c.replace("_SERVICE", " (Client)")}
        </button>`).join("");
  } catch (e) {
    console.error("Failed to load folders:", e);
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

  const sortedYears = Array.from(years).sort((a, b) => a - b);
  sortedYears.forEach(y => {
      const opt = document.createElement("option");
      opt.value = y;
      opt.innerText = y;
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
    const expA = new Date(a.expiry_date || "2099-01-01");
    const expB = new Date(b.expiry_date || "2099-01-01");
    if (sort === "updated_desc") return dateB - dateA;
    if (sort === "updated_asc") return dateA - dateB;
    if (sort === "exp_asc") return expA - expB;
    if (sort === "exp_desc") return expB - expA;
    if (sort === "serial_asc") return a.serial.localeCompare(b.serial);
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
      const updated = item.last_updated ? new Date(item.last_updated).toLocaleDateString() : "-";
      return `
          <tr class="hover:bg-gray-50 dark:hover:bg-gray-700 transition border-b border-gray-100 dark:border-gray-700 cursor-pointer" onclick="showDetails(${safeItem})">
              <td class="px-6 py-3 font-medium text-gray-900 dark:text-white">${item.serial}</td>
              <td class="px-6 py-3 text-gray-600 dark:text-gray-300">${item.model}</td>
              <td class="px-6 py-3"><span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200">${item.expiry_date}</span></td>
              <td class="px-6 py-3 text-xs text-gray-500 dark:text-gray-400">${updated}</td>
              <td class="px-6 py-3 text-right space-x-3" onclick="event.stopPropagation()">
                  ${item.pdf_url ? `<a href="${item.pdf_url}" target="_blank" class="text-blue-500 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300" title="PDF"><i class="fas fa-file-pdf"></i></a>` : ""}
                  <button onclick="deleteItem('${currentCollectionName}', '${item.id}')" class="text-red-400 hover:text-red-600 dark:hover:text-red-300" title="Delete"><i class="fas fa-trash"></i></button>
              </td>
          </tr>
      `;
    }).join("");
}

function showDetails(item) {
  const col = item.collection || currentCollectionName;
  currentCollectionName = col;

  document.getElementById("detSerial").value = item.serial;
  document.getElementById("detModel").value = item.model;
  document.getElementById("detCal").value = item.calibration_date || item.cal || "";
  document.getElementById("detExp").value = item.expiry_date || item.exp || "";
  document.getElementById("detCert").value = item.cert || "";
  document.getElementById("detLot").value = item.lot || "";
  document.getElementById("detUpdated").innerText = item.last_updated ? new Date(item.last_updated).toLocaleString() : "-";

  document.getElementById("btnViewPdf").href = item.pdf_url || "#";
  document.getElementById("btnViewQr").href = item.qr_image_url || "#";

  const qrLink = item.qr_link || `https://qrcertificates-30ddb.web.app/?id=${item.serial}`;
  const nfc = `${qrLink}\nCert:${item.cert || ""}\nSN:${item.serial}\nCal:${item.calibration_date || item.cal || ""}\nExp:${item.expiry_date || item.exp || ""}`;
  document.getElementById("detNfc").innerText = nfc;

  document.getElementById("detailModal").classList.remove("hidden");
}

async function updateEntry() {
  if (!currentCollectionName) return;
  const fd = new FormData();
  fd.append("collection", currentCollectionName);
  fd.append("serial", document.getElementById("detSerial").value);
  fd.append("model", document.getElementById("detModel").value);
  fd.append("cal", document.getElementById("detCal").value);
  fd.append("exp", document.getElementById("detExp").value);
  fd.append("cert", document.getElementById("detCert").value);
  fd.append("lot", document.getElementById("detLot").value);

  try {
    await axios.post(`${API}/api/update_record`, fd);
    alert("Entry Updated!");
    closeModal("detailModal");
    loadCollection(currentCollectionName);
  } catch (e) {
    alert("Update failed");
  }
}

async function handleFileSelect() {
    const files = document.getElementById("fileInput").files;
    if (!files.length) return;

    document.getElementById("loading").classList.remove("hidden");
    document.getElementById("btnReviewBatch").classList.add("hidden"); 

    batchResults = []; 

    for (let i = 0; i < files.length; i++) {
        const file = files[i];
        try {
            const extractFd = new FormData();
            extractFd.append("file", file);
            extractFd.append("is_service", isServiceUpload);

            document.getElementById("loadingText").innerText = `Analyzing PDF: ${file.name}...`;
            const resExt = await axios.post(`${API}/extract`, extractFd);
            
            if (resExt.data.status !== "success") continue;

            const itemsFound = resExt.data.data; 

            for (const item of itemsFound) {
                document.getElementById("loadingText").innerText = `Saving Item ${item.serial} from Page ${item.page}...`;
                const saveFd = new FormData();
                saveFd.append("file", file); 
                saveFd.append("serial", item.serial);
                saveFd.append("model", item.model);
                saveFd.append("cal", item.cal);
                saveFd.append("exp", item.exp);
                saveFd.append("cert", item.cert);
                saveFd.append("lot", item.lot);
                saveFd.append("page", item.page); 
                saveFd.append("collection", item.target_collection); 

                const saveRes = await axios.post(`${API}/save`, saveFd);

                batchResults.push({
                    ...item,
                    collection: item.target_collection,
                    pdf_url: saveRes.data.pdf_url, 
                    qr_link: saveRes.data.web_link,
                    qr_image_url: saveRes.data.qr_image_url,
                    last_updated: new Date().toISOString(),
                });
            }

            if (itemsFound.length > 0) {
                const last = itemsFound[itemsFound.length - 1];
                document.getElementById("upSerial").value = last.serial;
                document.getElementById("upModel").value = last.model;
                document.getElementById("upCal").value = last.cal;
                document.getElementById("upExp").value = last.exp;
                document.getElementById("upCert").value = last.cert;
                updatePreview();
            }
        } catch (e) {
            console.error("Error processing " + file.name, e);
            alert(`Error on ${file.name}: ${e.message}`);
        }
    }
    document.getElementById("loading").classList.add("hidden");
    document.getElementById("btnReviewBatch").classList.remove("hidden"); 
    document.getElementById("fileInput").value = "";
    openBatchReview();
}

function openBatchReview() {
  const tbody = document.getElementById("batchReviewBody");
  if (batchResults.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" class="p-4 text-center dark:text-gray-300">No results yet.</td></tr>`;
  } else {
    tbody.innerHTML = batchResults.map((item) => {
        const safeItem = JSON.stringify(item).replace(/"/g, "&quot;");
        return `
              <tr class="hover:bg-gray-50 dark:hover:bg-gray-700 cursor-pointer border-b dark:border-gray-600" onclick="showDetails(${safeItem})">
                  <td class="px-4 py-2 font-bold">${item.serial}</td>
                  <td class="px-4 py-2">${item.model}</td>
                  <td class="px-4 py-2">${item.cal}</td>
                  <td class="px-4 py-2">${item.exp}</td>
                  <td class="px-4 py-2 text-green-600 dark:text-green-400 font-bold"><i class="fas fa-check-circle"></i> Saved</td>
              </tr>`;
      }).join("");
  }
  document.getElementById("batchReviewModal").classList.remove("hidden");
}

function updatePreview() {
  const s = document.getElementById("upSerial").value || "SN...";
  const c = document.getElementById("upCert").value || "";
  const cal = document.getElementById("upCal").value || "";
  const exp = document.getElementById("upExp").value || "";
  const link = `https://qrcertificates-30ddb.web.app/?id=${encodeURIComponent(s)}`;
  document.getElementById("qrLinkPreview").innerText = link;
  const nfc = `${link}\nCert:${c}\nSN:${s}\nCal:${cal}\nExp:${exp}`;
  document.getElementById("nfcPreview").innerText = nfc;
}

async function saveManual() {
  const serial = document.getElementById("upSerial").value;
  if (!serial) { alert("Serial Number is required"); return; }
  const files = document.getElementById("fileInput").files;
  if (files.length > 0) handleFileSelect();
  else alert("Please select a PDF file to attach to this record.");
}

function resetUpload() {
  document.getElementById("fileInput").value = "";
  document.getElementById("fileNameDisplay").innerHTML = "Click or <b>Drag & Drop</b> PDF(s) here";
  document.getElementById("pdfPreviewFrame").innerHTML = "No PDF Selected";
  document.querySelectorAll("#tab-upload input:not([type=hidden])").forEach((i) => (i.value = ""));
  document.getElementById("btnReviewBatch").classList.add("hidden");
  currentFile = null;
  updatePreview();
}

function copyNfc() { copyText("nfcPreview"); }
function copyText(id) {
  const text = document.getElementById(id).innerText;
  navigator.clipboard.writeText(text);
  alert("Copied to clipboard!");
}
function closeModal(id) { document.getElementById(id).classList.add("hidden"); }

async function deleteItem(col, id) {
  if (!confirm("Delete this record permanently?")) return;
  await axios.delete(`${API}/api/collection/${col}/${id}`);
  loadCollection(col);
}

// --- Initialization Trigger ---
document.addEventListener('DOMContentLoaded', () => {
    initTheme();
    initDragAndDrop();
    init(); // Loads the folders
});
