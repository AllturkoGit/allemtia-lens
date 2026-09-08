let currentJobId = null;
let allResults = [];
let statusCheckInterval = null;
const API_URL = '/api';
let selectedImage = null;

// Radio button değişimlerini dinle
document.querySelectorAll('input[name="searchType"]').forEach(radio => {
    radio.addEventListener('change', function() {
        const imageGroup = document.getElementById('imageSearchGroup');
        const keywordGroup = document.getElementById('keywordSearchGroup');
        
        if (this.value === 'image') {
            imageGroup.style.display = 'block';
            keywordGroup.style.display = 'none';
        } else {
            imageGroup.style.display = 'none';
            keywordGroup.style.display = 'block';
        }
    });
});

// Drag and drop functionality
const imagePreview = document.getElementById('imagePreview');

['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
    imagePreview.addEventListener(eventName, preventDefaults, false);
    document.body.addEventListener(eventName, preventDefaults, false);
});

function preventDefaults(e) {
    e.preventDefault();
    e.stopPropagation();
}

['dragenter', 'dragover'].forEach(eventName => {
    imagePreview.addEventListener(eventName, highlight, false);
});

['dragleave', 'drop'].forEach(eventName => {
    imagePreview.addEventListener(eventName, unhighlight, false);
});

function highlight(e) {
    imagePreview.classList.add('drag-over');
}

function unhighlight(e) {
    imagePreview.classList.remove('drag-over');
}

imagePreview.addEventListener('drop', handleDrop, false);

function handleDrop(e) {
    const dt = e.dataTransfer;
    const files = dt.files;
    
    if (files.length > 0) {
        handleFile(files[0]);
    }
}

// Handle image selection
document.getElementById('imageInput').addEventListener('change', function(e) {
    const file = e.target.files[0];
    if (file) {
        handleFile(file);
    }
});

// Enter tuşu ile arama
document.getElementById('keywordInput').addEventListener('keypress', function(e) {
    if (e.key === 'Enter') {
        startSearch();
    }
});

function handleFile(file) {
    if (!file.type.startsWith('image/')) {
        showStatus('Lütfen bir resim dosyası seçin!', 'error');
        return;
    }
    
    if (file.size > 10 * 1024 * 1024) {
        showStatus('Dosya boyutu 10MB\'dan küçük olmalıdır!', 'error');
        return;
    }
    
    selectedImage = file;
    const reader = new FileReader();
    reader.onload = function(e) {
        document.getElementById('previewContent').innerHTML = `
            <img src="${e.target.result}" alt="Seçilen resim">
            <p style="margin-top: 16px; color: var(--primary); font-weight: 600;">
                ${file.name}
            </p>
        `;
    };
    reader.readAsDataURL(file);
}

function showStatus(message, type = 'loading') {
    const statusBar = document.getElementById('statusBar');
    statusBar.className = `status-bar active ${type}`;
    
    if (type === 'loading') {
        statusBar.innerHTML = `<span class="loading-spinner"></span>${message}`;
    } else {
        statusBar.innerHTML = message;
    }
}

function hideStatus() {
    const statusBar = document.getElementById('statusBar');
    statusBar.classList.remove('active');
}

async function startSearch() {
    const country = document.getElementById('country').value;
    const searchType = document.querySelector('input[name="searchType"]:checked').value;
    let sector = '';
    
    if (searchType === 'image') {
        if (!selectedImage) {
            showStatus('Lütfen bir resim seçin!', 'error');
            setTimeout(hideStatus, 3000);
            return;
        }

        // First analyze the image
        showStatus('Resim analiz ediliyor...', 'loading');
        
        const formData = new FormData();
        formData.append('image', selectedImage);
        
        try {
            const analyzeResponse = await fetch(`${API_URL}/analyze_image`, {
                method: 'POST',
                body: formData
            });
            
            const analyzeData = await analyzeResponse.json();
            
            if (!analyzeData.success) {
                showStatus('Hata: ' + analyzeData.error, 'error');
                setTimeout(hideStatus, 5000);
                return;
            }
            
            sector = analyzeData.product;
            document.getElementById('sector').value = sector;
            
            // Show product tag
            document.getElementById('productTag').textContent = sector;
            document.getElementById('productTag').style.display = 'inline-block';
            
            // Wait a bit to show the product name
            await new Promise(resolve => setTimeout(resolve, 1500));
        } catch (error) {
            showStatus('Resim analiz hatası: ' + error.message, 'error');
            return;
        }
    } else {
        // Keyword search
        sector = document.getElementById('keywordInput').value.trim();
        
        if (!sector) {
            showStatus('Lütfen bir anahtar kelime girin!', 'error');
            setTimeout(hideStatus, 3000);
            return;
        }
        
        document.getElementById('sector').value = sector;
        
        // Show product tag
        document.getElementById('productTag').textContent = sector;
        document.getElementById('productTag').style.display = 'inline-block';
    }

    // Reset
    allResults = [];
    document.getElementById('resultsBody').innerHTML = '';
    document.getElementById('resultCount').textContent = '0';
    document.getElementById('resultsSection').classList.add('active');
    document.getElementById('emptyState').style.display = 'block';
    
    // Disable button
    document.getElementById('searchBtn').disabled = true;
    
    showStatus('Arama başlatılıyor...', 'loading');

    try {
        const response = await fetch(`${API_URL}/start_scan`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                keyword: document.getElementById('sector').value,
                country: country
            })
        });

        const data = await response.json();
        
        if (data.success) {
            currentJobId = data.job_id;
            showStatus('Tarama devam ediyor...', 'loading');
            
            // Start checking status
            statusCheckInterval = setInterval(checkStatus, 1000);
        } else {
            showStatus('Hata: ' + (data.error || 'Bilinmeyen hata'), 'error');
            document.getElementById('searchBtn').disabled = false;
        }
    } catch (error) {
        showStatus('Bağlantı hatası: ' + error.message, 'error');
        document.getElementById('searchBtn').disabled = false;
    }
}

async function checkStatus() {
    if (!currentJobId) return;

    try {
        const response = await fetch(`${API_URL}/scan_status/${currentJobId}`);
        const data = await response.json();

        if (data.status === 'error') {
            clearInterval(statusCheckInterval);
            showStatus('Hata: ' + (data.error || 'Bilinmeyen hata'), 'error');
            document.getElementById('searchBtn').disabled = false;
            return;
        }

        // Add new rows
        if (data.new_rows && data.new_rows.length > 0) {
            const tbody = document.getElementById('resultsBody');
            document.getElementById('emptyState').style.display = 'none';
            
            data.new_rows.forEach((row, index) => {
                const tr = document.createElement('tr');
                tr.style.animationDelay = `${index * 0.05}s`;
                tr.innerHTML = `
                    <td>${row[0] || ''}</td>
                    <td>${row[1] ? `<a href="${row[1]}" target="_blank" class="website-link">${row[1]}</a>` : ''}</td>
                `;
                tbody.appendChild(tr);
                allResults.push(row);
            });
            
            document.getElementById('resultCount').textContent = allResults.length;
        }

        // Update status
        if (data.status === 'running') {
            showStatus(`Tarama devam ediyor... ${data.rows_found} şirket bulundu`, 'loading');
        } else if (data.status === 'completed') {
            clearInterval(statusCheckInterval);
            showStatus(`Tarama tamamlandı! ${data.rows_found} şirket bulundu`, 'success');
            document.getElementById('searchBtn').disabled = false;
            
            // Load all results if not already loaded
            if (data.rows && data.rows.length > allResults.length) {
                allResults = data.rows;
                const tbody = document.getElementById('resultsBody');
                tbody.innerHTML = '';
                document.getElementById('emptyState').style.display = 'none';
                
                data.rows.forEach((row, index) => {
                    const tr = document.createElement('tr');
                    tr.style.animationDelay = `${index * 0.05}s`;
                    tr.innerHTML = `
                        <td>${row[0] || ''}</td>
                        <td>${row[1] ? `<a href="${row[1]}" target="_blank" class="website-link">${row[1]}</a>` : ''}</td>
                    `;
                    tbody.appendChild(tr);
                });
                
                document.getElementById('resultCount').textContent = allResults.length;
            }
            
            if (allResults.length === 0) {
                document.getElementById('emptyState').style.display = 'block';
            }
        }
    } catch (error) {
        console.error('Status check error:', error);
    }
}