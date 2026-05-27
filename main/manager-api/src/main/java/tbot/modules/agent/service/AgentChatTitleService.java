package tbot.modules.agent.service;

import tbot.modules.agent.entity.AgentChatTitleEntity;

public interface AgentChatTitleService {

    void saveOrUpdateTitle(String sessionId, String title);

    String getTitleBySessionId(String sessionId);
}