package tbot.modules.device;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

class DeviceProfileMigrationTest {

    private static final Path MIGRATION = Path.of(
            "src/main/resources/db/changelog/202606191145.sql");
    private static final Path MASTER = Path.of(
            "src/main/resources/db/changelog/db.changelog-master.yaml");

    @Test
    @DisplayName("device child-profile migration adds expected columns in idempotent guarded blocks")
    void deviceChildProfileMigrationAddsExpectedColumnsWithGuards() throws IOException {
        String sql = Files.readString(MIGRATION, StandardCharsets.UTF_8);
        List<ColumnSpec> columns = List.of(
                new ColumnSpec("child_interests", "VARCHAR(255)", "child_age"),
                new ColumnSpec("learning_style", "VARCHAR(32)", "child_interests"),
                new ColumnSpec("vocabulary_level", "VARCHAR(32)", "learning_style"),
                new ColumnSpec("parent_career", "VARCHAR(64)", "vocabulary_level"));

        int lastColumnOffset = -1;
        for (ColumnSpec column : columns) {
            int columnOffset = sql.indexOf("COLUMN_NAME = '" + column.name + "'");
            assertTrue(columnOffset > lastColumnOffset, column.name + " guard should be in append order");
            lastColumnOffset = columnOffset;

            assertTrue(sql.contains("TABLE_NAME = 'ai_device' AND COLUMN_NAME = '" + column.name + "'"));
            assertTrue(sql.contains("ADD COLUMN `" + column.name + "` " + column.type));
            assertTrue(sql.contains("AFTER `" + column.after + "`"));
            assertTrue(sql.contains("SELECT ''Column " + column.name + " already exists'' AS msg"));
        }

        assertEquals(4, count(sql, "PREPARE stmt FROM @sql;"));
        assertEquals(4, count(sql, "EXECUTE stmt;"));
        assertEquals(4, count(sql, "DEALLOCATE PREPARE stmt;"));
    }

    @Test
    @DisplayName("device child-profile migration is registered in liquibase master changelog")
    void deviceChildProfileMigrationIsRegisteredInMasterChangelog() throws IOException {
        String master = Files.readString(MASTER, StandardCharsets.UTF_8);
        // Entity fields must never ship without a master-registered changeset, or
        // MyBatis SELECT lists fail with "Unknown column 'child_interests'".
        assertTrue(master.contains("id: 202606191145"),
                "changeSet id 202606191145 must be registered");
        assertTrue(master.contains("classpath:db/changelog/202606191145.sql"),
                "sqlFile path for 202606191145 must be registered");
        assertTrue(master.indexOf("id: 202606191030") < master.indexOf("id: 202606191145"),
                "202606191145 must run after 202606191030 (child_name/child_age)");
    }

    private static int count(String source, String needle) {
        Matcher matcher = Pattern.compile(Pattern.quote(needle)).matcher(source);
        int count = 0;
        while (matcher.find()) {
            count++;
        }
        return count;
    }

    private record ColumnSpec(String name, String type, String after) {
    }
}
