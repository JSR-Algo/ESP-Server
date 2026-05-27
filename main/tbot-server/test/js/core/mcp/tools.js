import { log } from '../../utils/logger.js?v=0205';

// ==========================================
// MCP ToolManageLogic
// ==========================================

// globalVariable
let mcpTools = [];
let mcpEditingIndex = null;
let mcpProperties = [];
let websocket = null; // will from externalSet

/**
 * Set WebSocket Instance
 * @param {WebSocket} ws - WebSocket ConnectInstance
 */
export function setWebSocket(ws) {
    websocket = ws;
}

/**
 * Initialize MCP Tool
 */
export async function initMcpTools() {
    // LoadDefaultToolData
    const defaultMcpTools = await fetch("js/config/default-mcp-tools.json").then(res => res.json());
    const savedTools = localStorage.getItem('mcpTools');
    if (savedTools) {
        try {
            const parsedTools = JSON.parse(savedTools);
            // mergeDefaultToolandUserSaveofTool,KeepUserCustomofTool
            const defaultToolNames = new Set(defaultMcpTools.map(t => t.name));
            // AddDefaultToolmidDoes not existnewTool
            parsedTools.forEach(tool => {
                if (!defaultToolNames.has(tool.name)) {
                    defaultMcpTools.push(tool);
                }
            });
            mcpTools = defaultMcpTools;
        } catch (e) {
            log('LoadMCPToolFail, useDefaultTool', 'warning');
            mcpTools = [...defaultMcpTools];
        }
    } else {
        mcpTools = [...defaultMcpTools];
    }
    renderMcpTools();
    setupMcpEventListeners();
}

/**
 * renderTool list
 */
function renderMcpTools() {
    const container = document.getElementById('mcpToolsContainer');
    const countSpan = document.getElementById('mcpToolsCount');
    if (!container) {
        return; // Container not found, skip rendering
    }
    if (countSpan) {
        countSpan.textContent = `${mcpTools.length} countTool`;
    }
    if (mcpTools.length === 0) {
        container.innerHTML = '<div style="text-align: center; padding: 30px; color: #999;">NoneTool,ClickbelowButtonAddnewTool</div>';
        return;
    }
    container.innerHTML = mcpTools.map((tool, index) => {
        const paramCount = tool.inputSchema.properties ? Object.keys(tool.inputSchema.properties).length : 0;
        const requiredCount = tool.inputSchema.required ? tool.inputSchema.required.length : 0;
        const hasMockResponse = tool.mockResponse && Object.keys(tool.mockResponse).length > 0;
        return `
            <div class="mcp-tool-card">
                <div class="mcp-tool-header">
                    <div class="mcp-tool-name">${tool.name}</div>
                    <div class="mcp-tool-actions">
                        <button class="mcp-edit-btn" onclick="window.mcpModule.editMcpTool(${index})">
                            ✏️ Edit
                        </button>
                        <button class="mcp-delete-btn" onclick="window.mcpModule.deleteMcpTool(${index})">
                            🗑️ Delete
                        </button>
                    </div>
                </div>
                <div class="mcp-tool-description">${tool.description}</div>
                <div class="mcp-tool-info">
                    <div class="mcp-tool-info-row">
                        <span class="mcp-tool-info-label">ParameterQuantity:</span>
                        <span class="mcp-tool-info-value">${paramCount} count ${requiredCount > 0 ? `(${requiredCount} countRequired)` : ''}</span>
                    </div>
                    <div class="mcp-tool-info-row">
                        <span class="mcp-tool-info-label">SimulateReturn:</span>
                        <span class="mcp-tool-info-value">${hasMockResponse ? '✅ alreadyConfig: ' + JSON.stringify(tool.mockResponse) : '⚪ UseDefault'}</span>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

/**
 * renderParameterList
 */
function renderMcpProperties() {
    const container = document.getElementById('mcpPropertiesContainer');
    const emptyState = document.getElementById('mcpEmptyState');
    if (!container) {
        return; // Container not found, skip rendering
    }
    if (mcpProperties.length === 0) {
        if (emptyState) {
            emptyState.style.display = 'block';
        }
        container.innerHTML = '';
        return;
    }
    if (emptyState) {
        emptyState.style.display = 'none';
    }
    container.innerHTML = mcpProperties.map((prop, index) => `
        <div class="mcp-property-card" onclick="window.mcpModule.editMcpProperty(${index})">
            <div class="mcp-property-row-label">
                <span class="mcp-property-label">ParameterName</span>
                <span class="mcp-property-value">${prop.name}${prop.required ? ' <span class="mcp-property-required-badge">[Required]</span>' : ''}</span>
            </div>
            <div class="mcp-property-row-label">
                <span class="mcp-property-label">DataType</span>
                <span class="mcp-property-value">${getTypeLabel(prop.type)}</span>
            </div>
            <div class="mcp-property-row-label">
                <span class="mcp-property-label">Description</span>
                <span class="mcp-property-value">${prop.description || '-'}</span>
            </div>
            <div class="mcp-property-row-action">
                <button class="mcp-property-delete-btn" onclick="event.stopPropagation(); window.mcpModule.deleteMcpProperty(${index})">Delete</button>
            </div>
        </div>
    `).join('');
}

/**
 * GetDataTypeTag
 */
function getTypeLabel(type) {
    const typeMap = {
        'string': 'String',
        'integer': 'integer',
        'number': 'Number',
        'boolean': 'Boolean',
        'array': 'Array',
        'object': 'Object'
    };
    return typeMap[type] || type;
}

/**
 * AddParameter - openParameterEditmodal
 */
function addMcpProperty() {
    openPropertyModal();
}

/**
 * Edit parameter - openParameterEditmodal
 */
function editMcpProperty(index) {
    openPropertyModal(index);
}

/**
 * openParameterEditmodal
 */
function openPropertyModal(index = null) {
    const form = document.getElementById('mcpPropertyForm');
    const title = document.getElementById('mcpPropertyModalTitle');
    document.getElementById('mcpPropertyIndex').value = index !== null ? index : -1;

    if (index !== null) {
        const prop = mcpProperties[index];
        title.textContent = 'Edit parameter';
        document.getElementById('mcpPropertyName').value = prop.name;
        document.getElementById('mcpPropertyType').value = prop.type || 'string';
        document.getElementById('mcpPropertyMinimum').value = prop.minimum !== undefined ? prop.minimum : '';
        document.getElementById('mcpPropertyMaximum').value = prop.maximum !== undefined ? prop.maximum : '';
        document.getElementById('mcpPropertyDescription').value = prop.description || '';
        document.getElementById('mcpPropertyRequired').checked = prop.required || false;
    } else {
        title.textContent = 'AddParameter';
        form.reset();
        document.getElementById('mcpPropertyName').value = `param_${mcpProperties.length + 1}`;
        document.getElementById('mcpPropertyType').value = 'string';
        document.getElementById('mcpPropertyMinimum').value = '';
        document.getElementById('mcpPropertyMaximum').value = '';
        document.getElementById('mcpPropertyDescription').value = '';
        document.getElementById('mcpPropertyRequired').checked = false;
    }

    updatePropertyRangeVisibility();
    document.getElementById('mcpPropertyModal').style.display = 'flex';
}

/**
 * CloseParameterEditmodal
 */
function closePropertyModal() {
    document.getElementById('mcpPropertyModal').style.display = 'none';
}

/**
 * UpdatevalueRangeInput boxofVisibleness
 */
function updatePropertyRangeVisibility() {
    const type = document.getElementById('mcpPropertyType').value;
    const rangeGroup = document.getElementById('mcpPropertyRangeGroup');
    if (type === 'integer' || type === 'number') {
        rangeGroup.style.display = 'block';
    } else {
        rangeGroup.style.display = 'none';
    }
}

/**
 * Handle ParametersFormSubmit
 */
function handlePropertySubmit(e) {
    e.preventDefault();
    const index = parseInt(document.getElementById('mcpPropertyIndex').value);
    const name = document.getElementById('mcpPropertyName').value.trim();
    const type = document.getElementById('mcpPropertyType').value;
    const minimum = document.getElementById('mcpPropertyMinimum').value;
    const maximum = document.getElementById('mcpPropertyMaximum').value;
    const description = document.getElementById('mcpPropertyDescription').value.trim();
    const required = document.getElementById('mcpPropertyRequired').checked;

    // CheckNamerepeat
    const isDuplicate = mcpProperties.some((p, i) => i !== index && p.name === name);
    if (isDuplicate) {
        alert('ParameterNameAlready exists, please usedifferentName');
        return;
    }

    const propData = {
        name,
        type,
        description,
        required
    };

    // numberValue typeAddRangelimit
    if (type === 'integer' || type === 'number') {
        if (minimum !== '') {
            propData.minimum = parseFloat(minimum);
        }
        if (maximum !== '') {
            propData.maximum = parseFloat(maximum);
        }
    }

    if (index >= 0) {
        mcpProperties[index] = propData;
    } else {
        mcpProperties.push(propData);
    }

    renderMcpProperties();
    closePropertyModal();
}

/**
 * DeleteParameter
 */
function deleteMcpProperty(index) {
    mcpProperties.splice(index, 1);
    renderMcpProperties();
}

/**
 * SeteventListen
 */
function setupMcpEventListeners() {
    const panel = document.getElementById('mcpToolsPanel');
    const addBtn = document.getElementById('addMcpToolBtn');
    const modal = document.getElementById('mcpToolModal');
    const closeBtn = document.getElementById('closeMcpModalBtn');
    const cancelBtn = document.getElementById('cancelMcpBtn');
    const form = document.getElementById('mcpToolForm');
    const addPropertyBtn = document.getElementById('addMcpPropertyBtn');

    // ParameterEditmodalRelatedelement
    const propertyModal = document.getElementById('mcpPropertyModal');
    const closePropertyBtn = document.getElementById('closeMcpPropertyModalBtn');
    const cancelPropertyBtn = document.getElementById('cancelMcpPropertyBtn');
    const propertyForm = document.getElementById('mcpPropertyForm');
    const propertyTypeSelect = document.getElementById('mcpPropertyType');

    // Return early if required elements don't exist (e.g., in test environment)
    if (!panel || !addBtn || !modal || !closeBtn || !cancelBtn || !form || !addPropertyBtn) {
        return;
    }
    addBtn.addEventListener('click', () => openMcpModal());
    closeBtn.addEventListener('click', closeMcpModal);
    cancelBtn.addEventListener('click', closeMcpModal);
    addPropertyBtn.addEventListener('click', addMcpProperty);
    form.addEventListener('submit', handleMcpSubmit);

    // ParameterEditmodal event
    if (propertyModal && closePropertyBtn && cancelPropertyBtn && propertyForm && propertyTypeSelect) {
        closePropertyBtn.addEventListener('click', closePropertyModal);
        cancelPropertyBtn.addEventListener('click', closePropertyModal);
        propertyForm.addEventListener('submit', handlePropertySubmit);
        propertyTypeSelect.addEventListener('change', updatePropertyRangeVisibility);
    }
}

/**
 * open modal
 */
function openMcpModal(index = null) {
    const isConnected = websocket && websocket.readyState === WebSocket.OPEN;
    if (isConnected) {
        alert('WebSocket Connected, unableEditTool');
        return;
    }
    mcpEditingIndex = index;
    const errorContainer = document.getElementById('mcpErrorContainer');
    errorContainer.innerHTML = '';
    if (index !== null) {
        document.getElementById('mcpModalTitle').textContent = 'EditTool';
        const tool = mcpTools[index];
        document.getElementById('mcpToolName').value = tool.name;
        document.getElementById('mcpToolDescription').value = tool.description;
        document.getElementById('mcpMockResponse').value = tool.mockResponse ? JSON.stringify(tool.mockResponse, null, 2) : '';
        mcpProperties = [];
        const schema = tool.inputSchema;
        if (schema.properties) {
            Object.keys(schema.properties).forEach(key => {
                const prop = schema.properties[key];
                mcpProperties.push({
                    name: key,
                    type: prop.type || 'string',
                    minimum: prop.minimum,
                    maximum: prop.maximum,
                    description: prop.description || '',
                    required: schema.required && schema.required.includes(key)
                });
            });
        }
    } else {
        document.getElementById('mcpModalTitle').textContent = 'AddTool';
        document.getElementById('mcpToolForm').reset();
        mcpProperties = [];
    }
    renderMcpProperties();
    document.getElementById('mcpToolModal').style.display = 'flex';
}

/**
 * Closemodal
 */
function closeMcpModal() {
    document.getElementById('mcpToolModal').style.display = 'none';
    mcpEditingIndex = null;
    document.getElementById('mcpToolForm').reset();
    mcpProperties = [];
    document.getElementById('mcpErrorContainer').innerHTML = '';
}

/**
 * ProcessFormSubmit
 */
function handleMcpSubmit(e) {
    e.preventDefault();
    const errorContainer = document.getElementById('mcpErrorContainer');
    errorContainer.innerHTML = '';
    const name = document.getElementById('mcpToolName').value.trim();
    const description = document.getElementById('mcpToolDescription').value.trim();
    const mockResponseText = document.getElementById('mcpMockResponse').value.trim();
    // CheckNamerepeat
    const isDuplicate = mcpTools.some((tool, index) => tool.name === name && index !== mcpEditingIndex);
    if (isDuplicate) {
        showMcpError('Tool nameAlready exists, please usedifferentName');
        return;
    }
    // ParseSimulateReturnResult
    let mockResponse = null;
    if (mockResponseText) {
        try {
            mockResponse = JSON.parse(mockResponseText);
        } catch (e) {
            showMcpError('SimulateReturnResultNotValidof JSON Format: ' + e.message);
            return;
        }
    }
    // Build inputSchema
    const inputSchema = { type: "object", properties: {}, required: [] };
    mcpProperties.forEach(prop => {
        const propSchema = { type: prop.type };
        if (prop.description) {
            propSchema.description = prop.description;
        }
        if ((prop.type === 'integer' || prop.type === 'number')) {
            if (prop.minimum !== undefined && prop.minimum !== '') {
                propSchema.minimum = prop.minimum;
            }
            if (prop.maximum !== undefined && prop.maximum !== '') {
                propSchema.maximum = prop.maximum;
            }
        }
        inputSchema.properties[prop.name] = propSchema;
        if (prop.required) {
            inputSchema.required.push(prop.name);
        }
    });
    if (inputSchema.required.length === 0) {
        delete inputSchema.required;
    }
    const tool = { name, description, inputSchema, mockResponse };
    if (mcpEditingIndex !== null) {
        mcpTools[mcpEditingIndex] = tool;
        log(`UpdatedTool: ${name}`, 'success');
    } else {
        mcpTools.push(tool);
        log(`alreadyAddTool: ${name}`, 'success');
    }
    saveMcpTools();
    renderMcpTools();
    closeMcpModal();
}

/**
 * Showerror
 */
function showMcpError(message) {
    const errorContainer = document.getElementById('mcpErrorContainer');
    errorContainer.innerHTML = `<div class="mcp-error">${message}</div>`;
}

/**
 * EditTool
 */
function editMcpTool(index) {
    openMcpModal(index);
}

/**
 * DeleteTool
 */
function deleteMcpTool(index) {
    const isConnected = websocket && websocket.readyState === WebSocket.OPEN;
    if (isConnected) {
        alert('WebSocket Connected, unableEditTool');
        return;
    }
    if (confirm(`Confirm deleteTool "${mcpTools[index].name}" ?`)) {
        const toolName = mcpTools[index].name;
        mcpTools.splice(index, 1);
        saveMcpTools();
        renderMcpTools();
        log(`alreadyDeleteTool: ${toolName}`, 'info');
    }
}

/**
 * SaveTool
 */
function saveMcpTools() {
    localStorage.setItem('mcpTools', JSON.stringify(mcpTools));
}

/**
 * Get tool list
 */
export function getMcpTools() {
    return mcpTools.map(tool => ({ name: tool.name, description: tool.description, inputSchema: tool.inputSchema }));
}

/**
 * ExecuteTool call
 */
export async function executeMcpTool(toolName, toolArgs) {
    const tool = mcpTools.find(t => t.name === toolName);
    if (!tool) {
        log(`Not foundTool: ${toolName}`, 'error');
        return { success: false, error: `UnknownTool: ${toolName}` };
    }

    // Processtake photoTool
    if (toolName === 'self_camera_take_photo') {
        if (typeof window.takePhoto === 'function') {
            const question = toolArgs && toolArgs.question ? toolArgs.question : 'Descriptionitem seen';
            log(`runningExecutetake photo: ${question}`, 'info');
            const result = await window.takePhoto(question);
            return result;
        } else {
            log('take photoFunctionunavailable', 'warning');
            return { success: false, error: 'camera notStartor notSupporttake photoFunction' };
        }
    }

    // If hasSimulateReturnResult, useit
    if (tool.mockResponse) {
        // Replace template variables
        let responseStr = JSON.stringify(tool.mockResponse);
        // replace ${paramName} Format ofVariable
        if (toolArgs) {
            Object.keys(toolArgs).forEach(key => {
                const regex = new RegExp(`\\$\\{${key}\\}`, 'g');
                responseStr = responseStr.replace(regex, toolArgs[key]);
            });
        }
        try {
            const response = JSON.parse(responseStr);
            log(`Tool ${toolName} ExecuteSuccess,ReturnSimulateResult: ${responseStr}`, 'success');
            return response;
        } catch (e) {
            log(`ParseSimulateReturnResultFail: ${e.message}`, 'error');
            return tool.mockResponse;
        }
    }
    // NoneSimulateReturnResult,ReturnDefaultSuccessmessage
    log(`Tool ${toolName} ExecuteSuccess,ReturnDefaultResult`, 'success');
    return { success: true, message: `Tool ${toolName} ExecuteSuccess`, tool: toolName, arguments: toolArgs };
}

// expose globalMethodfor HTML inline eventCall
window.mcpModule = { addMcpProperty, editMcpProperty, deleteMcpProperty, editMcpTool, deleteMcpTool };
