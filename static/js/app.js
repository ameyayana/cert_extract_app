const API = ""; 
let currentCollection = "GD";
let currentFile = null;

const collections = [
    { id: 'GD', label: 'GD', icon: 'fa-folder' },
    { id: 'GD (Client)', label: 'GD (Client)', icon: 'fa-tools' },
    { id: 'EEBD', label: 'EEBD', icon: 'fa-folder' },
    { id: 'HARNESS', label: 'HARNESS', icon: 'fa-folder' },
    { id: 'ABSORBER', label: 'ABSORBER', icon: 'fa-folder' },
    { id: 'SCBA', label: 'SCBA', icon: 'fa-folder' }
];

async function init() {
    renderSidebar();
    if (localStorage.getItem('theme') === 'dark' || (!localStorage.getItem('theme') && window.matchMedia('(prefers-color-scheme: dark)').matches)) {
        document.documentElement.classList.add('dark');
    }
    loadCollection(currentCollection);
}

function renderSidebar() {
    const nav = document.getElementById('folderList');
    nav.innerHTML = collections.map(col => `
        <button onclick="loadCollection('${col.id}')" data-id="${col.id}" class="folder-btn w-full flex items-center justify-between px-4 py-3 rounded-xl transition group">
            <div class="flex items-center gap-3">
                <i class="fas ${col.icon} ${col.id.includes('Client') ? 'text-purple-500' : 'text-yellow-500'} opacity-70 group-hover:opacity-100"></i>
                <span class="text-sm font-medium">${col.label}</span>
            </div>
        </button>
    `).join('');
}

function toggleTheme() {
    document.documentElement.classList.toggle('dark');
    localStorage.setItem('theme', document.documentElement.classList.contains('dark') ? 'dark' : 'light');
}

async function loadCollection(col) {
    currentCollection = col;
    document.querySelectorAll('.folder-btn').forEach(btn => btn.classList.toggle('bg-slate-800', btn.getAttribute('data-id') === col));
    document.getElementById("collectionTitle").innerText = col;
    const tbody = document.getElementById("tableBody");
    tbody.innerHTML = `<tr><td colspan="4" class="py-10 text-center"><i class="fas fa-spinner fa-spin mr-2"></i>Loading...</td></tr>`;

    try {
        const res = await axios.get(`${API}/api/collection/${col}`);
        const items = res.data;
        document.getElementById("itemCount").innerText = `${items.length} items`;
        tbody.innerHTML = items.map(item => `
            <tr class="border-b border-slate-700/50 hover:bg-slate-800/50 transition">
                <td class="py-4 px-6 font-bold">${item.serial || 'N/A'}</td>
                <td class="py-4 px-6 text-slate-400">${item.model || '-'}</td>
                <td class="py-4 px-6"><span class="px-3 py-1 rounded-full text-xs bg-green-900/30 text-green-400 border border-green-500/20">${item.expiry || '-'}</span></td>
                <td class="py-4 px-6 flex justify-center gap-2">
                    <button onclick="deleteItem('${col}', '${item.id}')" class="p-2 text-red-500 hover:bg-red-500/10 rounded-lg"><i class="fas fa-trash"></i></button>
                </td>
            </tr>`).join("");
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="4" class="py-10 text-center text-red-500">Error loading data.</td></tr>`;
    }
}

async function deleteItem(col, id) {
    if (!confirm("Delete this record permanently?")) return;
    await axios.delete(`${API}/api/collection/${col}/${id}`);
    loadCollection(col);
}

function showTab(tab) {
    document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
    document.getElementById(`tab-${tab}`).classList.remove('hidden');
}

init();
