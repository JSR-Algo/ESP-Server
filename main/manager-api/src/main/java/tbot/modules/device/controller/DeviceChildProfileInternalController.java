package tbot.modules.device.controller;

import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;

import jakarta.validation.Valid;
import tbot.common.utils.Result;
import tbot.modules.device.dto.DeviceChildProfileProjectionDTO;
import tbot.modules.device.service.DeviceChildProfileProjectionService;
import tbot.modules.device.service.DeviceChildProfileProjectionService.DeviceNotFoundException;
import tbot.modules.device.service.DeviceChildProfileProjectionService.ProjectionConflictException;

@RestController
@RequestMapping("/internal/devices")
public class DeviceChildProfileInternalController {
    private final DeviceChildProfileProjectionService service;

    public DeviceChildProfileInternalController(DeviceChildProfileProjectionService service) {
        this.service = service;
    }

    @PutMapping("/{deviceId}/child-profile")
    public Result<DeviceChildProfileProjectionService.Outcome> replace(
            @PathVariable String deviceId,
            @Valid @RequestBody DeviceChildProfileProjectionDTO request) {
        return new Result<DeviceChildProfileProjectionService.Outcome>().ok(service.apply(deviceId, request));
    }

    @ExceptionHandler(ProjectionConflictException.class)
    ResponseEntity<Result<Void>> conflict(ProjectionConflictException exception) {
        return ResponseEntity.status(HttpStatus.CONFLICT)
                .body(new Result<Void>().error(HttpStatus.CONFLICT.value(), exception.getMessage()));
    }

    @ExceptionHandler(DeviceNotFoundException.class)
    ResponseEntity<Result<Void>> notFound(DeviceNotFoundException exception) {
        return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(new Result<Void>().error(HttpStatus.NOT_FOUND.value(), exception.getMessage()));
    }

    @ExceptionHandler(IllegalArgumentException.class)
    ResponseEntity<Result<Void>> invalid(IllegalArgumentException exception) {
        return ResponseEntity.badRequest()
                .body(new Result<Void>().error(HttpStatus.BAD_REQUEST.value(), exception.getMessage()));
    }
}
