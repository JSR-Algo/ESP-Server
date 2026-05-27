package tbot.modules.device.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Getter;
import lombok.Setter;

import java.io.Serializable;
import java.util.List;

@Setter
@Getter
@Schema(description = "Device firmware info report request body")
public class DeviceReportReqDTO implements Serializable {
    private static final long serialVersionUID = 1L;
    // region Entity Attribute
    @Schema(description = "Board firmware version")
    private Integer version;

    @Schema(description = "Flash size (unit: bytes)")
    @JsonProperty("flash_size")
    private Integer flashSize;

    @Schema(description = "Minimum free heap memory (bytes)")
    @JsonProperty("minimum_free_heap_size")
    private Integer minimumFreeHeapSize;

    @Schema(description = "Device MAC address")
    @JsonProperty("mac_address")
    private String macAddress;

    @Schema(description = "Device unique identifier UUID")
    private String uuid;

    @Schema(description = "Chip model name")
    @JsonProperty("chip_model_name")
    private String chipModelName;

    @Schema(description = "Chip details")
    @JsonProperty("chip_info")
    private ChipInfo chipInfo;

    @Schema(description = "Application info")
    private Application application;

    @Schema(description = "Partition table list")
    @JsonProperty("partition_table")
    private List<Partition> partitionTable;

    @Schema(description = "Current running OTA partition info")
    private OtaInfo ota;

    @Schema(description = "Board config info")
    private BoardInfo board;

    // endregion

    @Getter
    @Setter
    @Schema(description = "Chip info")
    public static class ChipInfo {
        @Schema(description = "Chip model code")
        private Integer model;

        @Schema(description = "Core count")
        private Integer cores;

        @Schema(description = "Hardware revision version")
        private Integer revision;

        @Schema(description = "Chip feature flags")
        private Integer features;
    }

    @Getter
    @Setter
    @Schema(description = "Board build info")
    public static class Application {
        @Schema(description = "Name")
        private String name;

        @Schema(description = "App version number")
        private String version;

        @Schema(description = "Build time (UTC ISO format)")
        @JsonProperty("compile_time")
        private String compileTime;

        @Schema(description = "ESP-IDF version")
        @JsonProperty("idf_version")
        private String idfVersion;

        @Schema(description = "ELF file SHA256 checksum")
        @JsonProperty("elf_sha256")
        private String elfSha256;
    }

    @Getter
    @Setter
    @Schema(description = "Partition info")
    public static class Partition {
        @Schema(description = "Partition label name")
        private String label;

        @Schema(description = "Partition type")
        private Integer type;

        @Schema(description = "Subtype")
        private Integer subtype;

        @Schema(description = "Start address")
        private Integer address;

        @Schema(description = "Partition size")
        private Integer size;
    }

    @Getter
    @Setter
    @Schema(description = "OTA info")
    public static class OtaInfo {
        @Schema(description = "Current OTA tag")
        private String label;
    }

    @Getter
    @Setter
    @Schema(description = "Board connection and network info")
    public static class BoardInfo {
        @Schema(description = "Board type")
        private String type;

        @Schema(description = "Connected Wi-Fi SSID")
        private String ssid;

        @Schema(description = "Wi-Fi signal strength (RSSI)")
        private Integer rssi;

        @Schema(description = "Wi-Fi channel")
        private Integer channel;

        @Schema(description = "IP address")
        private String ip;

        @Schema(description = "MAC address")
        private String mac;
    }
}
