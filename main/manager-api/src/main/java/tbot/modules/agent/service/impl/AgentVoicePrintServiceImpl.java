package tbot.modules.agent.service.impl;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.List;
import java.util.concurrent.Executor;
import java.util.stream.Collectors;

import org.apache.commons.lang3.StringUtils;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.RestTemplate;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;

import lombok.extern.slf4j.Slf4j;
import tbot.common.constant.Constant;
import tbot.common.exception.ErrorCode;
import tbot.common.exception.RenException;
import tbot.common.utils.ConvertUtils;
import tbot.common.utils.JsonUtils;
import tbot.modules.agent.dao.AgentVoicePrintDao;
import tbot.modules.agent.dto.AgentVoicePrintSaveDTO;
import tbot.modules.agent.dto.AgentVoicePrintUpdateDTO;
import tbot.modules.agent.dto.IdentifyVoicePrintResponse;
import tbot.modules.agent.entity.AgentVoicePrintEntity;
import tbot.modules.agent.service.AgentChatAudioService;
import tbot.modules.agent.service.AgentChatHistoryService;
import tbot.modules.agent.service.AgentVoicePrintService;
import tbot.modules.agent.vo.AgentVoicePrintVO;
import tbot.modules.sys.service.SysParamsService;

/**
 * @author zjy
 */
@Service
@Slf4j
public class AgentVoicePrintServiceImpl extends ServiceImpl<AgentVoicePrintDao, AgentVoicePrintEntity>
        implements AgentVoicePrintService {
    private final AgentChatAudioService agentChatAudioService;
    private final RestTemplate restTemplate;
    private final SysParamsService sysParamsService;
    private final AgentChatHistoryService agentChatHistoryService;
    // SpringbootProvided programmatic transaction class
    private final TransactionTemplate transactionTemplate;
    // Recognition
    private final Double RECOGNITION = 0.5;
    private final Executor taskExecutor;

    public AgentVoicePrintServiceImpl(AgentChatAudioService agentChatAudioService, RestTemplate restTemplate,
                                      SysParamsService sysParamsService, AgentChatHistoryService agentChatHistoryService,
                                      TransactionTemplate transactionTemplate, @Qualifier("taskExecutor") Executor taskExecutor) {
        this.agentChatAudioService = agentChatAudioService;
        this.restTemplate = restTemplate;
        this.sysParamsService = sysParamsService;
        this.agentChatHistoryService = agentChatHistoryService;
        this.transactionTemplate = transactionTemplate;
        this.taskExecutor = taskExecutor;
    }

    @Override
    public boolean insert(AgentVoicePrintSaveDTO dto) {
        // Get audio data
        ByteArrayResource resource = getVoicePrintAudioWAV(dto.getAgentId(), dto.getAudioId());
        // Recognize thisVoiceWhetherRegisterpassed
        IdentifyVoicePrintResponse response = identifyVoicePrint(dto.getAgentId(), resource);
        if (response != null && response.getScore() > RECOGNITION) {
            // Based on recognized voiceprintIDQuery corresponding userInfo
            AgentVoicePrintEntity existingVoicePrint = baseMapper.selectById(response.getSpeakerId());
            String existingUserName = existingVoicePrint != null ? existingVoicePrint.getSourceName() : "Unknown user";
            throw new RenException(ErrorCode.VOICEPRINT_ALREADY_REGISTERED, existingUserName);
        }
        AgentVoicePrintEntity entity = ConvertUtils.sourceToTarget(dto, AgentVoicePrintEntity.class);
        // Start Transaction
        return Boolean.TRUE.equals(transactionTemplate.execute(status -> {
            try {
                // SaveVoiceprintInfo
                int row = baseMapper.insert(entity);
                // Insert one record, affected data not equal to1Indicates appeared,SaveRollback on Issue
                if (row != 1) {
                    status.setRollbackOnly(); // Mark transaction rollback
                    return false;
                }
                // SendRegisterVoiceprint Request
                registerVoicePrint(entity.getId(), resource);
                return true;
            } catch (RenException e) {
                status.setRollbackOnly(); // Mark transaction rollback
                throw e;
            } catch (Exception e) {
                status.setRollbackOnly(); // Mark transaction rollback
                log.error("Save voiceprint error reason: {}", e.getMessage());
                throw new RenException(ErrorCode.VOICE_PRINT_SAVE_ERROR);
            }
        }));
    }

    @Override
    public boolean delete(Long userId, String voicePrintId) {
        // Start Transaction
        boolean b = Boolean.TRUE.equals(transactionTemplate.execute(status -> {
            try {
                // DeleteVoiceprint,According to specified currentLoginUser and agent
                int row = baseMapper.delete(new LambdaQueryWrapper<AgentVoicePrintEntity>()
                        .eq(AgentVoicePrintEntity::getId, voicePrintId)
                        .eq(AgentVoicePrintEntity::getCreator, userId));
                if (row != 1) {
                    status.setRollbackOnly(); // Mark transaction rollback
                    return false;
                }

                return true;
            } catch (Exception e) {
                status.setRollbackOnly(); // Mark transaction rollback
                log.error("Error reason for deleting voiceprint: {}", e.getMessage());
                throw new RenException(ErrorCode.VOICEPRINT_DELETE_ERROR);
            }
        }));
        // Database voiceprint dataDeleteContinue only if successDeleteVoiceprint service data
        if(b){
            taskExecutor.execute(()-> {
                try {
                    cancelVoicePrint(voicePrintId);
                }catch (RuntimeException e) {
                    log.error("Runtime error reason deleting voiceprint: {}, id: {}", e.getMessage(),voicePrintId);
                }
            });
        }
        return b;
    }

    @Override
    public List<AgentVoicePrintVO> list(Long userId, String agentId) {
        // According to specified currentLoginUser and agent lookup data
        List<AgentVoicePrintEntity> list = baseMapper.selectList(new LambdaQueryWrapper<AgentVoicePrintEntity>()
                .eq(AgentVoicePrintEntity::getAgentId, agentId)
                .eq(AgentVoicePrintEntity::getCreator, userId));
        return list.stream().map(entity -> {
            // Traverse and convert toAgentVoicePrintVOType
            return ConvertUtils.sourceToTarget(entity, AgentVoicePrintVO.class);
        }).toList();

    }

    @Override
    public boolean update(Long userId, AgentVoicePrintUpdateDTO dto) {
        AgentVoicePrintEntity agentVoicePrintEntity = baseMapper
                .selectOne(new LambdaQueryWrapper<AgentVoicePrintEntity>()
                        .eq(AgentVoicePrintEntity::getId, dto.getId())
                        .eq(AgentVoicePrintEntity::getCreator, userId));
        if (agentVoicePrintEntity == null) {
            return false;
        }
        // Get audioId
        String audioId = dto.getAudioId();
        // GetAgent id
        String agentId = agentVoicePrintEntity.getAgentId();
        ByteArrayResource resource;
        // audioIdnot equal empty, andaudioIdand PreviousSaveAudio ofidIf different, need reacquire audio data to generate voiceprint
        if (!StringUtils.isEmpty(audioId) && !audioId.equals(agentVoicePrintEntity.getAudioId())) {
            resource = getVoicePrintAudioWAV(agentId, audioId);

            // Recognize thisVoiceWhetherRegisterpassed
            IdentifyVoicePrintResponse response = identifyVoicePrint(agentId, resource);
            // Return score higher thanRECOGNITIONIndicates this voiceprint already exists
            if (response != null && response.getScore() > RECOGNITION) {
                // Judge returnedidIf not wantModifyVoiceprint ofidMeans this voiceprintid, now needRegisterofVoiceAlready exists and not original voiceprint, not allowedModify
                if (!response.getSpeakerId().equals(dto.getId())) {
                    // Based on recognized voiceprintIDQuery corresponding userInfo
                    AgentVoicePrintEntity existingVoicePrint = baseMapper.selectById(response.getSpeakerId());
                    String existingUserName = existingVoicePrint != null ? existingVoicePrint.getSourceName() : "Unknown user";
                    throw new RenException(ErrorCode.VOICEPRINT_UPDATE_NOT_ALLOWED, existingUserName);
                }
            }
        } else {
            resource = null;
        }
        // Start Transaction
        return Boolean.TRUE.equals(transactionTemplate.execute(status -> {
            try {
                AgentVoicePrintEntity entity = ConvertUtils.sourceToTarget(dto, AgentVoicePrintEntity.class);
                int row = baseMapper.updateById(entity);
                if (row != 1) {
                    status.setRollbackOnly(); // Mark transaction rollback
                    return false;
                }
                if (resource != null) {
                    String id = entity.getId();
                    // Unregister this voiceprint firstidvoiceprint vector on
                    cancelVoicePrint(id);
                    // SendRegisterVoiceprint Request
                    registerVoicePrint(id, resource);
                }
                return true;
            } catch (RenException e) {
                status.setRollbackOnly(); // Mark transaction rollback
                throw e;
            } catch (Exception e) {
                status.setRollbackOnly(); // Mark transaction rollback
                log.error("Modify voiceprint error reason: {}", e.getMessage());
                throw new RenException(ErrorCode.VOICEPRINT_UPDATE_ADMIN_ERROR);
            }
        }));
    }

    /**
     * Get voiceprint interfaceURIObject
     *
     * @return URIObject
     */
    private URI getVoicePrintURI() {
        // Get voiceprint API address
        String voicePrint = sysParamsService.getValue(Constant.SERVER_VOICE_PRINT, true);
        try {
            return new URI(voicePrint);
        } catch (URISyntaxException e) {
            log.error("Path format incorrect path: {},\nerror info:{}", voicePrint, e.getMessage());
                throw new RenException(ErrorCode.VOICEPRINT_API_URI_ERROR);
        }
    }

    /**
     * Get voiceprint address base path
     * 
     * @param uri Voiceprint Addressuri
     * @return Base Path
     */
    private String getBaseUrl(URI uri) {
        String protocol = uri.getScheme();
        String host = uri.getHost();
        int port = uri.getPort();
        if (port == -1) {
            return "%s://%s".formatted(protocol, host);
        } else {
            return "%s://%s:%s".formatted(protocol, host, port);
        }
    }

    /**
     * Get VerificationAuthorization
     *
     * @param uri Voiceprint Addressuri
     * @return Authorizationvalue
     */
    private String getAuthorization(URI uri) {
        // Get Parameters
        String query = uri.getQuery();
        // GetaesEncryptKey
        String str = "key=";
        return "Bearer " + query.substring(query.indexOf(str) + str.length());
    }

    /**
     * Get voiceprint audio resource data
     *
     * @param audioId AudioId
     * @return Voiceprint audio resource data
     */
    private ByteArrayResource getVoicePrintAudioWAV(String agentId, String audioId) {
        // Check whether this audio isBelongs toCurrent agent
        boolean b = agentChatHistoryService.isAudioOwnedByAgent(audioId, agentId);
        if (!b) {
            throw new RenException(ErrorCode.VOICEPRINT_AUDIO_NOT_BELONG_AGENT);
        }
        // Got audio data
        byte[] audio = agentChatAudioService.getAudio(audioId);
        // If audio data empty, error directly and do not continue
        if (audio == null || audio.length == 0) {
            throw new RenException(ErrorCode.VOICEPRINT_AUDIO_EMPTY);
        }
        // Wrap byte array as resource, return
        return new ByteArrayResource(audio) {
            @Override
            public String getFilename() {
                return "VoicePrint.WAV"; // SetFilename
            }
        };
    }

    /**
     * SendRegisterVoiceprinthttpRequest
     * 
     * @param id       Voiceprintid
     * @param resource Voiceprint audio resource
     */
    private void registerVoicePrint(String id, ByteArrayResource resource) {
        // Handle voiceprint API address, get prefix
        URI uri = getVoicePrintURI();
        String baseUrl = getBaseUrl(uri);
        String requestUrl = baseUrl + "/voiceprint/register";
        // Create request body
        MultiValueMap<String, Object> body = new LinkedMultiValueMap<>();
        body.add("speaker_id", id);
        body.add("file", resource);

        // CreateRequest header
        HttpHeaders headers = new HttpHeaders();
        headers.set("Authorization", getAuthorization(uri));
        headers.setContentType(MediaType.MULTIPART_FORM_DATA);
        // Create request body
        HttpEntity<MultiValueMap<String, Object>> requestEntity = new HttpEntity<>(body, headers);
        // Send POST Request
        ResponseEntity<String> response = restTemplate.postForEntity(requestUrl, requestEntity, String.class);

        if (response.getStatusCode() != HttpStatus.OK) {
            log.error("Voiceprint registration failed, request path: {}", requestUrl);
            throw new RenException(ErrorCode.VOICEPRINT_REGISTER_REQUEST_ERROR);
        }
        // CheckResponseContent
        String responseBody = response.getBody();
        if (responseBody == null || !responseBody.contains("true")) {
            log.error("Voiceprint registration failed, request handling failed content: {}", responseBody == null ? "Empty content" : responseBody);
            throw new RenException(ErrorCode.VOICEPRINT_REGISTER_PROCESS_ERROR);
        }
    }

    /**
     * Send unregister voiceprint request
     * 
     * @param voicePrintId Voiceprintid
     */
    private void cancelVoicePrint(String voicePrintId) {
        URI uri = getVoicePrintURI();
        String baseUrl = getBaseUrl(uri);
        String requestUrl = baseUrl + "/voiceprint/" + voicePrintId;
        // CreateRequest header
        HttpHeaders headers = new HttpHeaders();
        headers.set("Authorization", getAuthorization(uri));
        // Create request body
        HttpEntity<MultiValueMap<String, Object>> requestEntity = new HttpEntity<>(headers);

        // Send POST Request
        ResponseEntity<String> response = restTemplate.exchange(requestUrl, HttpMethod.DELETE, requestEntity,
                String.class);
        if (response.getStatusCode() != HttpStatus.OK) {
            log.error("Voiceprint deregistration failed, request path: {}", requestUrl);
            throw new RenException(ErrorCode.VOICEPRINT_UNREGISTER_REQUEST_ERROR);
        }
        // CheckResponseContent
        String responseBody = response.getBody();
        if (responseBody == null || !responseBody.contains("true")) {
            log.error("Voiceprint deregistration failed, request handling failed content: {}", responseBody == null ? "Empty content" : responseBody);
            throw new RenException(ErrorCode.VOICEPRINT_UNREGISTER_PROCESS_ERROR);
        }
    }

    /**
     * Send voiceprint recognitionhttpRequest
     * 
     * @param agentId  Agent id
     * @param resource Voiceprint audio resource
     * @return Return recognition data
     */
    private IdentifyVoicePrintResponse identifyVoicePrint(String agentId, ByteArrayResource resource) {

        // Get all for this agentRegisterVoiceprint of
        List<AgentVoicePrintEntity> agentVoicePrintList = baseMapper
                .selectList(new LambdaQueryWrapper<AgentVoicePrintEntity>()
                        .select(AgentVoicePrintEntity::getId)
                        .eq(AgentVoicePrintEntity::getAgentId, agentId));

        // VoiceprintQuantityfor0Means not yetRegisterPassed voiceprint needs no recognition request
        if (agentVoicePrintList.isEmpty()) {
            return null;
        }
        // Handle voiceprint API address, get prefix
        URI uri = getVoicePrintURI();
        String baseUrl = getBaseUrl(uri);
        String requestUrl = baseUrl + "/voiceprint/identify";
        // Create request body
        MultiValueMap<String, Object> body = new LinkedMultiValueMap<>();

        // Createspeaker_idParameter
        String speakerIds = agentVoicePrintList.stream()
                .map(AgentVoicePrintEntity::getId)
                .collect(Collectors.joining(","));
        body.add("speaker_ids", speakerIds);
        body.add("file", resource);

        // CreateRequest header
        HttpHeaders headers = new HttpHeaders();
        headers.set("Authorization", getAuthorization(uri));
        headers.setContentType(MediaType.MULTIPART_FORM_DATA);
        // Create request body
        HttpEntity<MultiValueMap<String, Object>> requestEntity = new HttpEntity<>(body, headers);
        // Send POST Request
        ResponseEntity<String> response = restTemplate.postForEntity(requestUrl, requestEntity, String.class);

        if (response.getStatusCode() != HttpStatus.OK) {
            log.error("Voiceprint recognition request failed, request path: {}", requestUrl);
            throw new RenException(ErrorCode.VOICEPRINT_IDENTIFY_REQUEST_ERROR);
        }
        // CheckResponseContent
        String responseBody = response.getBody();
        if (responseBody != null) {
            return JsonUtils.parseObject(responseBody, IdentifyVoicePrintResponse.class);
        }
        return null;
    }
}
