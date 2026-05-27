package tbot.modules.config.controller;

import java.util.List;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import lombok.AllArgsConstructor;
import tbot.common.utils.Result;
import tbot.common.validator.ValidatorUtils;
import tbot.modules.config.dto.AgentModelsDTO;
import tbot.modules.config.dto.CorrectWordsDTO;
import tbot.modules.config.service.ConfigService;

/**
 * tbot-server Config Get
 *
 * @since 1.0.0
 */
@RestController
@RequestMapping("config")
@Tag(name = "Parameter management")
@AllArgsConstructor
public class ConfigController {
    private final ConfigService configService;

    @PostMapping("server-base")
    @Operation(summary = "Server get config API")
    public Result<Object> getConfig() {
        Object config = configService.getConfig(true);
        return new Result<Object>().ok(config);
    }

    @PostMapping("agent-models")
    @Operation(summary = "Get agent model")
    public Result<Object> getAgentModels(@Valid @RequestBody AgentModelsDTO dto) {
        // Validate Data
        ValidatorUtils.validateEntity(dto);
        Object models = configService.getAgentModels(dto.getMacAddress(), dto.getSelectedModule());
        return new Result<Object>().ok(models);
    }

    @PostMapping("correct-words")
    @Operation(summary = "Get agent replacement words")
    public Result<Object> getCorrectWords(@Valid @RequestBody CorrectWordsDTO dto) {
        ValidatorUtils.validateEntity(dto);
        List<String> list = configService.getCorrectWords(dto.getMacAddress());
        return new Result<Object>().ok(list);
    }
}
