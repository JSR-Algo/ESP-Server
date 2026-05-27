package tbot.modules.correctword.service;

import java.util.List;
import java.util.Map;

import tbot.common.page.PageData;
import tbot.modules.correctword.dto.CorrectWordFileCreateDTO;
import tbot.modules.correctword.vo.CorrectWordFileVO;
import tbot.modules.correctword.vo.CorrectWordSimpleVO;

public interface CorrectWordFileService {

    /**
     * Create replacement word file
     *
     * @param dto Create Parameters
     * @return FileVO
     */
    CorrectWordFileVO createFile(CorrectWordFileCreateDTO dto);

    /**
     * Modify replacement word file (full replacement entries)
     *
     * @param fileId FileID
     * @param dto    Modify Parameters
     */
    void updateFile(String fileId, CorrectWordFileCreateDTO dto);

    /**
     * Get current user's replacement word file list
     *
     * @param params Pagination Parameters
     * @return Paged Data
     */
    PageData<CorrectWordFileVO> listFiles(Map<String, Object> params);

    /**
     * Get current user's replacement word file list (no pagination, for dropdown selection)
     *
     * @return File list
     */
    List<CorrectWordFileVO> listAllFiles();

    /**
     * Get raw file content (for download)
     *
     * @param fileId FileID
     * @return File Entity
     */
    CorrectWordFileVO getFileContent(String fileId);

    /**
     * Delete replacement word file and all its entries and related records
     *
     * @param fileId FileID
     */
    void deleteFile(String fileId);

    /**
     * Delete replacement word file association records linked to agent (do not delete file itself)
     *
     * @param agentId AgentID
     */
    void deleteMappingsByAgentId(String agentId);

    /**
     * Get all replacement word entries of agent (lite version, for device side)
     *
     * @param agentId AgentID
     * @return Replacement word list
     */
    List<CorrectWordSimpleVO> getAllItemsByAgentId(String agentId);

    /**
     * Get replacement word file associated with agentIDList
     *
     * @param agentId AgentID
     * @return FileIDList
     */
    List<String> getAgentCorrectWordFileIds(String agentId);

    /**
     * Save agent-associated replacement word file (full replacement)
     *
     * @param agentId AgentID
     * @param fileIds FileIDList
     */
    void saveAgentCorrectWords(String agentId, List<String> fileIds);

    /**
     * Batch delete replacement word files
     *
     * @param fileIds FileIDList
     */
    void batchDeleteFiles(List<String> fileIds);
}
