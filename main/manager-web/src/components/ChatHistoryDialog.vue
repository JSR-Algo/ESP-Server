<template>
    <el-dialog
        :title="$t('chatHistory.with') + agentName + $t('chatHistory.dialogTitle') + (currentMacAddress ? '[' + currentMacAddress + ']' : '')"
        :visible.sync="dialogVisible" width="80%" :before-close="handleClose" custom-class="chat-history-dialog">
        <div class="chat-container">
            <div class="session-list" @scroll="handleScroll">
                <div v-for="session in sessions" :key="session.sessionId" class="session-item"
                    :class="{ active: currentSessionId === session.sessionId }" @click="selectSession(session)">
                    <img :src="getUserAvatar(session.sessionId)" class="avatar" />
                    <div class="session-info">
                        <div class="session-time">{{ session.title || formatTime(session.createdAt) }}</div>
                        <div class="message-count">{{ session.chatCount > 99 ? '99' : session.chatCount }}</div>
                    </div>
                </div>
                <div v-if="loading" class="loading">{{ $t('chatHistory.loading') }}</div>
                <div v-if="!hasMore" class="no-more">{{ $t('chatHistory.noMoreRecords') }}</div>
            </div>
            <div class="chat-content">
                <div v-if="currentSessionId" class="messages">
                    <div v-for="(message, index) in messagesWithTime" :key="message.id">
                        <div v-if="message.type === 'time'" class="time-divider">
                            {{ message.content }}
                        </div>
                        <div v-else class="message-item" :class="{ 'user-message': message.chatType === 1, 'tool-message': message.chatType === 3 }">
                            <img :src="message.chatType === 1 ? getUserAvatar(currentSessionId) : require('@/assets/tbot-logo.png')"
                                class="avatar" />
                            <div class="message-content">
                                <template v-if="Array.isArray(extractContentFromString(message.content))">
                                    <div class="content-wrapper">
                                        <div v-for="(item, idx) in extractContentFromString(message.content)" :key="idx">
                                            <div v-if="item.type === 'text'" class="text-content">{{ item.text }}</div>
                                            <div v-else-if="item.type === 'tool'" class="tool-call-text">{{ item.text }}</div>
                                            <div v-else-if="item.type === 'tool_result'" class="tool-call-text">
                                                <div v-if="item.text && item.text.length > 80" class="tool-result-wrapper">
                                                    <div v-if="isToolResultCollapsed(index, idx)" class="tool-result-collapsed">
                                                        {{ getFirstLineText(item.text) }}
                                                    </div>
                                                    <div v-else class="tool-result-expanded">
                                                        {{ item.text }}
                                                    </div>
                                                    <span class="tool-toggle-btn" @click="toggleToolResult(index, idx)">
                                                        <i :class="isToolResultCollapsed(index, idx) ? 'el-icon-arrow-down' : 'el-icon-arrow-up'"></i>
                                                    </span>
                                                </div>
                                                <div v-else>{{ item.text }}</div>
                                            </div>
                                        </div>
                                    </div>
                                </template>
                                <template v-else>
                                    {{ extractContentFromString(message.content) }}
                                </template>
                                <i v-if="message.audioId" :class="getAudioIconClass(message)"
                                    @click="playAudio(message)" class="audio-icon"></i>
                            </div>
                        </div>
                    </div>
                </div>
                <div v-else class="no-session-selected">
                    {{ $t('chatHistory.selectSession') }}
                </div>
            </div>
        </div>
        <div v-if="currentSessionId" class="download-buttons">
            <el-button type="primary" plain size="small" @click="downloadCurrentSessionWithPrevious">
                {{ $t('chatHistory.downloadCurrentWithPreviousSessions') }}
            </el-button>
            <el-button type="primary" plain size="small" @click="downloadCurrentSession">
                {{ $t('chatHistory.downloadCurrentSession') }}
            </el-button>
        </div>
    </el-dialog>
</template>

<script>
import { debounce } from '@/utils'
import Api from '@/apis/api';

export default {
    name: 'ChatHistoryDialog',
    props: {
        visible: {
            type: Boolean,
            default: false
        },
        agentId: {
            type: String,
            required: true
        },
        agentName: {
            type: String,
            required: true
        }
    },
    data() {
        return {
            dialogVisible: false,
            sessions: [],
            messages: [],
            currentSessionId: '',
            currentMacAddress: '',
            page: 1,
            limit: 20,
            loading: false,
            hasMore: true,
            scrollTimer: null,
            isFirstLoad: true,
            playingAudioId: null,
            audioElement: null,
            expandedToolResults: {} // Track tool result expansionStatus
        };
    },
    watch: {
        visible(val) {
            this.dialogVisible = val;
            if (val) {
                this.resetData();
                this.loadSessions();
            } else {
                this.audioElement?.pause();
                this.audioElement = null;
                this.playingAudioId = null;
            }
        },
        dialogVisible(val) {
            if (!val) {
                this.$emit('update:visible', false);
            }
        }
    },
    computed: {
        messagesWithTime() {
            if (!this.messages || this.messages.length === 0) return [];

            const result = [];
            const TIME_INTERVAL = 60 * 1000; // 1minute interval (milliseconds)

            // Add first itemMessagetime marker
            if (this.messages[0]) {
                result.push({
                    type: 'time',
                    content: this.formatTime(this.messages[this.messages.length - 1].createdAt),
                    id: `time-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`
                });
            }

            // ProcessMessage list
            for (let i = 0; i < this.messages.length; i++) {
                const currentMessage = this.messages[i];
                result.push(currentMessage);

                // Check whether need add time marker
                if (i < this.messages.length - 1) {
                    const currentTime = new Date(currentMessage.createdAt).getTime();
                    const nextTime = new Date(this.messages[i + 1].createdAt).getTime();

                    if (nextTime - currentTime > TIME_INTERVAL) {
                        result.push({
                            type: 'time',
                            content: this.formatTime(this.messages[i + 1].createdAt),
                            id: `time-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`
                        });
                    }
                }
            }

            return result;
        }
    },
    methods: {
        /**
         * from content Extract from fieldChat content
         * If content is JSON Format (such as {"speaker": "Unknown speaker", "content": "What time is it now."}), then extract content Field
         * If content If normal string, return directly
         * 
         * @param {string} content OriginalContent
         * @returns {string} ExtractedChat content
         */
        extractContentFromString(content) {
            if (!content || content.trim() === '') {
                return content;
            }

            // Try parse as JSON
            try {
                const jsonObj = JSON.parse(content);

                // If array format (contains text and tool)
                if (Array.isArray(jsonObj)) {
                    return jsonObj;
                }

                // If object and has content Field
                if (jsonObj && typeof jsonObj === 'object' && jsonObj.content) {
                    return jsonObj.content;
                }
            } catch (e) {
                // If not valid JSONReturn original directlyContent
            }

            // If not JSON format or no content Field, return original directlyContent
            return content;
        },
        // Toggle tool result expansion/CollapseStatus
        toggleToolResult(messageIndex, itemIndex) {
            const key = `${messageIndex}-${itemIndex}`;
            this.$set(this.expandedToolResults, key, !this.expandedToolResults[key]);
        },
        // Determine whether tool result is foldedStatus
        isToolResultCollapsed(messageIndex, itemIndex) {
            const key = `${messageIndex}-${itemIndex}`;
            // Default collapsed (truemeans collapsed)
            return !this.expandedToolResults[key];
        },
        // Get truncated text (show only first line)
        getFirstLineText(text) {
            if (!text) return '';
            const firstLine = text.split('\n')[0];
            return firstLine.length < text.length ? firstLine + '...' : text;
        },
        resetData() {
            this.sessions = [];
            this.messages = [];
            this.currentSessionId = '';
            this.currentMacAddress = '';
            this.page = 1;
            this.loading = false;
            this.hasMore = true;
            this.isFirstLoad = true;
            this.expandedToolResults = {};
        },
        handleClose() {
            this.dialogVisible = false;
        },
        loadSessions() {
            if (this.loading || (!this.isFirstLoad && !this.hasMore)) {
                return;
            }

            this.loading = true;
            const params = {
                page: this.page,
                limit: this.limit
            };

            Api.agent.getAgentSessions(this.agentId, params, (res) => {
                if (res.data && res.data.data && Array.isArray(res.data.data.list)) {
                    const list = res.data.data.list;
                    this.hasMore = list.length === this.limit;

                    this.sessions = [...this.sessions, ...list];
                    this.page++;

                    if (this.sessions.length > 0 && !this.currentSessionId) {
                        this.selectSession(this.sessions[0]);
                    }
                }
                this.loading = false;
                this.isFirstLoad = false;
            });
        },
        selectSession(session) {
            this.currentSessionId = session.sessionId;
            Api.agent.getAgentChatHistory(this.agentId, session.sessionId, (res) => {
                if (res.data && res.data.data) {
                    this.messages = res.data.data;
                    if (this.messages.length > 0 && this.messages[0].macAddress) {
                        this.currentMacAddress = this.messages[0].macAddress;
                    }
                    // Update chat records in session listQuantity
                    this.sessions = this.sessions.map(item => {
                        if (item.sessionId === session.sessionId) {
                            item.chatCount = this.messages.length;
                        }
                        return item;
                    })
                }
            });
        },
        handleScroll(e) {
            if (this.scrollTimer) {
                clearTimeout(this.scrollTimer);
            }

            this.scrollTimer = setTimeout(() => {
                const { scrollTop, scrollHeight, clientHeight } = e.target;
                // Load more when scrolled to bottom
                if (scrollHeight - scrollTop <= clientHeight + 50) {
                    this.loadSessions();
                }
            }, 200);
        },
        formatTime(timestamp) {
            const date = new Date(timestamp);
            const now = new Date();
            const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
            const yesterday = new Date(today);
            yesterday.setDate(yesterday.getDate() - 1);

            const hours = date.getHours().toString().padStart(2, '0');
            const minutes = date.getMinutes().toString().padStart(2, '0');

            if (date >= today) {
                return `${this.$t('chatHistory.today')} ${hours}:${minutes}`;
            } else if (date >= yesterday) {
                return `${this.$t('chatHistory.yesterday')} ${hours}:${minutes}`;
            } else {
                const year = date.getFullYear();
                const month = (date.getMonth() + 1).toString().padStart(2, '0');
                const day = date.getDate().toString().padStart(2, '0');
                return `${year}-${month}-${day} ${hours}:${minutes}`;
            }
        },
        getAudioIconClass(message) {
            if (this.playingAudioId === message.audioId) {
                return 'el-icon-loading';
            }
            return 'el-icon-video-play';
        },
        playAudio: debounce(function(message) {
            if (this.playingAudioId === message.audioId) {
                // If current audio playing, stop playback
                if (this.audioElement) {
                    this.audioElement.pause();
                    this.audioElement = null;
                }
                this.playingAudioId = null;
                return;
            }

            // Stop currently playing audio
            if (this.audioElement) {
                this.audioElement.pause();
                this.audioElement = null;
            }

            // firstGet audio download ID
            this.playingAudioId = message.audioId;
            Api.agent.getAudioId(message.audioId, (res) => {
                if (res.data && res.data.data) {
                    if (!this.audioElement) {
                        this.audioElement = new Audio();
                    }
                    
                    // Use obtained downloadIDPlay audio
                    this.audioElement.src = Api.getServiceUrl() + `/agent/play/${res.data.data}`;
                    this.audioElement.onended = () => {
                        this.playingAudioId = null;
                        this.audioElement = null;
                    };

                    this.audioElement.play();
                }
            });
        }, 300),
        getUserAvatar(sessionId) {
            // from sessionId Extract all numbers from
            const numbers = sessionId.match(/\d+/g);
            if (!numbers) return require('@/assets/user-avatar1.png');

            // Add all numbers
            const sum = numbers.reduce((acc, num) => acc + parseInt(num), 0);

            // Compute mod5And add1, get1-5Number between
            const avatarIndex = (sum % 5) + 1;

            // Return correspondingAvatarImage
            return require(`@/assets/user-avatar${avatarIndex}.png`);
        },

        // Download this session chat records
        downloadCurrentSession() {
            Api.agent.getDownloadUrl(this.agentId, this.currentSessionId, (res) => {
                if (res && res.data && res.data.code === 0 && res.data.data) {
                    const uuid = res.data.data;
                    window.open(`${Api.getServiceUrl()}/agent/chat-history/download/${uuid}/current`, '_blank');
                } else {
                    this.$message.error(this.$t('chatHistory.downloadLinkFailed'));
                }
            });
        },

        // Download this session and previous 20 chat records
        downloadCurrentSessionWithPrevious() {
            Api.agent.getDownloadUrl(this.agentId, this.currentSessionId, (res) => {
                if (res && res.data && res.data.code === 0 && res.data.data) {
                    const uuid = res.data.data;
                    window.open(`${Api.getServiceUrl()}/agent/chat-history/download/${uuid}/previous`, '_blank');
                } else {
                    this.$message.error(this.$t('chatHistory.downloadLinkFailed'));
                }
            });
        }
    }
};
</script>

<style scoped>
.chat-container {
    display: flex;
    height: 100%;
}

.session-list {
    width: 250px;
    border-right: 1px solid #eee;
    overflow-y: auto;
    padding: 10px;
}

.session-item {
    display: flex;
    align-items: center;
    padding: 10px;
    cursor: pointer;
    border-radius: 8px;
    margin-bottom: 10px;
}

.session-item:hover {
    background-color: #f5f5f5;
}

.session-item.active {
    background-color: #e6f7ff;
}

.avatar {
    width: 40px;
    height: 40px;
    border-radius: 50%;
    margin-right: 10px;
}

.session-info {
    width: calc(100% - 50px);
}

.session-time {
    font-size: 14px;
    color: #272727;
    float: left;
    height: 30px;
    line-height: 30px;
    width: calc(100% - 30px);
    /* forMessageQuantityReserve space */
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.message-count {
    font-size: 14px;
    color: #fff;
    background-color: #b4b4b4;
    border-radius: 20px;
    float: left;
    width: 20px;
    height: 20px;
    line-height: 20px;
    margin-top: 5px;
    margin-left: 5px;
}

.chat-content {
    flex: 1;
    padding: 20px;
    overflow-y: auto;
}

.message-item {
    display: flex;
    margin-bottom: 20px;
}

.message-item.user-message {
    flex-direction: row-reverse;
}

.message-content {
    max-width: 60%;
    padding: 10px 15px;
    border-radius: 8px;
    background-color: #f0f0f0;
    margin: 0 10px;
    text-align: left;
    line-height: 20px;
    position: relative;
    display: flex;
    align-items: center;
}

.audio-icon {
    font-size: 20px;
    cursor: pointer;
    margin: 0 5px;
    color: #1890ff;
}

.user-message .message-content {
    background-color: #1890ff;
    color: white;
    flex-direction: row-reverse;
}

.user-message .audio-icon {
    color: white;
}

.content-wrapper {
    width: 100%;
}

.text-content {
    display: block;
    margin-bottom: 4px;
}

.tool-call-text {
    color: #1890ff;
    font-family: 'Courier New', monospace;
    font-weight: 500;
    font-size: 12px;
    display: block;
    margin-top: 4px;
}

.user-message .tool-call-text {
    color: #e6f7ff;
}

.tool-message .message-content {
    background-color: #f0f0f0;
}

.tool-result-wrapper {
    position: relative;
    padding-right: 20px;
}

.tool-result-collapsed {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.tool-toggle-btn {
    position: absolute;
    right: 0;
    top: 0;
    cursor: pointer;
    color: #1890ff;
    font-size: 12px;
}

.tool-toggle-btn:hover {
    color: #40a9ff;
}

.loading,
.no-more {
    text-align: center;
    padding: 10px 10px 30px 10px;
    color: #999;
}

.no-session-selected {
    display: flex;
    justify-content: center;
    align-items: center;
    height: 100%;
    color: #999;
}

.time-divider {
    text-align: center;
    margin: 10px 0;
    color: #999;
    font-size: 12px;
}

.time-divider::before,
.time-divider::after {
    content: '';
    display: inline-block;
    width: 30%;
    height: 1px;
    background-color: #eee;
    vertical-align: middle;
    margin: 0 10px;
}

.download-buttons {
    padding: 20px;
    display: flex;
    gap: 10px;
    border-top: 1px solid #eee;
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    background-color: white;
}

.download-buttons .el-button {
    flex: 1;
}
</style>

<style>
.chat-history-dialog {
    display: flex;
    flex-direction: column;
    min-width: 700px;
    margin: 0 !important;
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    height: 98vh;
    max-width: 85vw;
    border-radius: 12px;
    overflow: hidden;
}

.chat-history-dialog .el-dialog__header {
    background-color: #e6f7ff;
    padding: 15px 20px;
}

.chat-history-dialog .el-dialog__body {
    padding: 0;
    overflow: hidden;
    height: calc(90vh - 54px);
    /* SubtractTitleBar height */
}
</style>