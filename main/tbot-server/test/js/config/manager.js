// ConfigManageModule

// Generate RandomMACAddress
function generateRandomMac() {
    const hexDigits = '0123456789ABCDEF';
    let mac = '';
    for (let i = 0; i < 6; i++) {
        if (i > 0) mac += ':';
        for (let j = 0; j < 2; j++) {
            mac += hexDigits.charAt(Math.floor(Math.random() * 16));
        }
    }
    return mac;
}

// LoadConfig
export function loadConfig() {
    const deviceMacInput = document.getElementById('deviceMac');
    const deviceNameInput = document.getElementById('deviceName');
    const clientIdInput = document.getElementById('clientId');
    const otaUrlInput = document.getElementById('otaUrl');

    // fromlocalStorageLoadMACAddress,If nonethenGeneratenew
    let savedMac = localStorage.getItem('xz_tester_deviceMac');
    if (!savedMac) {
        savedMac = generateRandomMac();
        localStorage.setItem('xz_tester_deviceMac', savedMac);
    }
    deviceMacInput.value = savedMac;

    // fromlocalStorageLoadOtherConfig
    const savedDeviceName = localStorage.getItem('xz_tester_deviceName');
    if (savedDeviceName) {
        deviceNameInput.value = savedDeviceName;
    }

    const savedClientId = localStorage.getItem('xz_tester_clientId');
    if (savedClientId) {
        clientIdInput.value = savedClientId;
    }

    const savedOtaUrl = localStorage.getItem('xz_tester_otaUrl');
    if (savedOtaUrl) {
        otaUrlInput.value = savedOtaUrl;
    }
}

// Save config
export function saveConfig() {
    const deviceMacInput = document.getElementById('deviceMac');
    const deviceNameInput = document.getElementById('deviceName');
    const clientIdInput = document.getElementById('clientId');

    localStorage.setItem('xz_tester_deviceMac', deviceMacInput.value);
    localStorage.setItem('xz_tester_deviceName', deviceNameInput.value);
    localStorage.setItem('xz_tester_clientId', clientIdInput.value);
}

// Get Configvalue
export function getConfig() {
    // fromDOMGetvalue
    const deviceMac = document.getElementById('deviceMac')?.value.trim() || '';
    const deviceName = document.getElementById('deviceName')?.value.trim() || '';
    const clientId = document.getElementById('clientId')?.value.trim() || '';

    return {
        deviceId: deviceMac,  // UseMACAddressAsdeviceId
        deviceName,
        deviceMac,
        clientId
    };
}

// SaveConnectURL
export function saveConnectionUrls() {
    const otaUrl = document.getElementById('otaUrl').value.trim();
    const wsUrl = document.getElementById('serverUrl').value.trim();
    localStorage.setItem('xz_tester_otaUrl', otaUrl);
    localStorage.setItem('xz_tester_wsUrl', wsUrl);
}
