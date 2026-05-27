package tbot.modules.knowledge.task;

import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import lombok.AllArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import tbot.modules.knowledge.service.KnowledgeFilesService;

/**
 * Knowledge base documentStatusSync scheduled task
 * 
 * Purpose:
 * 1. Auto scan in "RUNNING" (Parsing) StatusofDocument
 * 2. Call RAGFlow API get latestStatus
 * 3. StatusFlip (RUNNING -> SUCCESS/FAIL) When, sync update database
 * 4. [Key] On parse success, compensateUpdate knowledge baseStats ofInfo (TokenCount)
 */
@Component
@AllArgsConstructor
@Slf4j
public class DocumentStatusSyncTask {

    private final KnowledgeFilesService knowledgeFilesService;

    /**
     * each 30 Sync every seconds
     * Adopt fixedDelayEnsure previous execution finished 30 Start next only after seconds, prevent backlog
     */
    @Scheduled(fixedDelay = 30000)
    public void syncRunningDocuments() {
        try {
            // log.debug("Start document status sync task...");
            knowledgeFilesService.syncRunningDocuments();
        } catch (Exception e) {
            log.error("Document status sync task exception", e);
        }
    }
}
