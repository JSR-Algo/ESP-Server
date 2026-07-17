package tbot.modules.device;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.Statement;
import java.util.List;
import java.time.Year;

import javax.sql.DataSource;

import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.testcontainers.containers.MySQLContainer;
import org.mybatis.spring.SqlSessionFactoryBean;
import org.mybatis.spring.SqlSessionTemplate;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

import com.mysql.cj.jdbc.MysqlDataSource;

import liquibase.integration.spring.SpringLiquibase;
import tbot.modules.device.dao.DeviceDao;
import tbot.modules.device.dto.DeviceChildProfileProjectionDTO;
import tbot.modules.device.dto.DeviceChildProfileProjectionDTO.Profile;
import tbot.modules.device.service.DeviceChildProfileProjectionService;
import tbot.modules.robot.projection.ChildProfileProjectionCanonicalizer;

class DeviceChildProfileMigrationTest {
    private static final String CHANGELOG = "db/changelog/db.changelog-master.yaml";
    private static final MySQLContainer<?> MYSQL = new MySQLContainer<>("mysql:8.4")
            .withDatabaseName("manager")
            .withUsername("manager")
            .withPassword("manager");

    @BeforeAll
    static void start() {
        MYSQL.start();
    }

    @AfterAll
    static void stop() {
        MYSQL.stop();
    }

    @Test
    void migrationBackfillsLegacyRowsAndSupportsRollbackReapply() throws Exception {
        DataSource dataSource = dataSource();
        SpringLiquibase liquibase = new SpringLiquibase();
        liquibase.setDataSource(dataSource);
        liquibase.setChangeLog("classpath:" + CHANGELOG);
        liquibase.setTestRollbackOnUpdate(true);
        liquibase.afterPropertiesSet();

        try (Connection connection = dataSource.getConnection()) {
            try (Statement statement = connection.createStatement()) {
                statement.executeUpdate("INSERT INTO ai_device (id, child_name, child_age, child_interests, learning_style, vocabulary_level, parent_career) VALUES ('legacy', 'Old', 9, 'music', 'visual', 'basic', 'teacher')");
            }
            assertSchemaAndBackfill(connection);

            liquibase.setTestRollbackOnUpdate(false);
            liquibase.afterPropertiesSet();
            assertEquals(1, columnCount(connection, "child_profile_revision"));

            exerciseMapperTransaction(dataSource, connection);
        }
    }

    private static void exerciseMapperTransaction(DataSource dataSource, Connection connection) throws Exception {
        try (Statement statement = connection.createStatement()) {
            statement.executeUpdate("INSERT INTO ai_device (id) VALUES ('mapped-device')");
        }
        SqlSessionFactoryBean factoryBean = new SqlSessionFactoryBean();
        factoryBean.setDataSource(dataSource);
        factoryBean.setMapperLocations(new ClassPathResource("mapper/device/DeviceDao.xml"));
        SqlSessionTemplate session = new SqlSessionTemplate(factoryBean.getObject());
        DeviceChildProfileProjectionService service = new DeviceChildProfileProjectionService(session.getMapper(DeviceDao.class));
        TransactionTemplate transaction = new TransactionTemplate(new DataSourceTransactionManager(dataSource));

        Profile profile = new Profile("123e4567-e89b-12d3-a456-426614174000", "An", 2018,
                List.of("music", "robots"), "visual", "beginner", "engineer");
        String replaceHash = ChildProfileProjectionCanonicalizer.canonicalize("replace", 1, profile.toCanonicalProfile()).sha256();
        transaction.executeWithoutResult(status -> service.apply("mapped-device",
                new DeviceChildProfileProjectionDTO("replace", 1, replaceHash, profile)));

        try (Statement statement = connection.createStatement();
                ResultSet result = statement.executeQuery("SELECT * FROM ai_device WHERE id='mapped-device'")) {
            result.next();
            assertEquals(profile.childProfileId(), result.getString("child_profile_id"));
            assertEquals(2018, result.getInt("child_birth_year"));
            assertEquals("An", result.getString("child_name"));
            assertEquals(Year.now().getValue() - 2018, result.getInt("child_age"));
            assertEquals("music,robots", result.getString("child_interests"));
            assertEquals(1, result.getLong("child_profile_revision"));
            assertEquals(replaceHash, result.getString("child_profile_payload_hash"));
        }

        String clearHash = ChildProfileProjectionCanonicalizer.canonicalize("clear", 2, null).sha256();
        transaction.executeWithoutResult(status -> service.apply("mapped-device",
                new DeviceChildProfileProjectionDTO("clear", 2, clearHash, null)));
        try (Statement statement = connection.createStatement();
                ResultSet result = statement.executeQuery("SELECT * FROM ai_device WHERE id='mapped-device'")) {
            result.next();
            assertNull(result.getString("child_profile_id"));
            assertNull(result.getObject("child_birth_year"));
            assertNull(result.getString("child_name"));
            assertNull(result.getObject("child_age"));
            assertNull(result.getString("child_interests"));
            assertNull(result.getString("learning_style"));
            assertNull(result.getString("vocabulary_level"));
            assertNull(result.getString("parent_career"));
            assertEquals(2, result.getLong("child_profile_revision"));
            assertEquals(clearHash, result.getString("child_profile_payload_hash"));
        }
    }

    private static DataSource dataSource() {
        MysqlDataSource dataSource = new MysqlDataSource();
        dataSource.setUrl(MYSQL.getJdbcUrl());
        dataSource.setUser(MYSQL.getUsername());
        dataSource.setPassword(MYSQL.getPassword());
        return dataSource;
    }

    private static void assertSchemaAndBackfill(Connection connection) throws Exception {
        assertColumn(connection, "child_profile_id", "char", 36L, "ascii", "ascii_bin", null, "YES");
        assertColumn(connection, "child_birth_year", "int", null, null, null, null, "YES");
        assertColumn(connection, "child_profile_revision", "bigint", null, null, null, "-1", "NO");
        assertColumn(connection, "child_profile_payload_hash", "varchar", 64L, "ascii", "ascii_bin", null, "YES");
        try (Statement statement = connection.createStatement();
                ResultSet result = statement.executeQuery("SELECT child_profile_revision, child_profile_id, child_birth_year, child_profile_payload_hash FROM ai_device WHERE id='legacy'")) {
            result.next();
            assertEquals(-1L, result.getLong("child_profile_revision"));
            assertNull(result.getString("child_profile_id"));
            assertNull(result.getObject("child_birth_year"));
            assertNull(result.getString("child_profile_payload_hash"));
        }
    }

    private static void assertColumn(Connection connection, String name, String dataType, Long length,
            String charset, String collation, String defaultValue, String nullable) throws Exception {
        String sql = "SELECT DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, CHARACTER_SET_NAME, COLLATION_NAME, COLUMN_DEFAULT, IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='ai_device' AND COLUMN_NAME='" + name + "'";
        try (Statement statement = connection.createStatement(); ResultSet result = statement.executeQuery(sql)) {
            result.next();
            assertEquals(dataType, result.getString("DATA_TYPE"));
            if (length == null) assertNull(result.getObject("CHARACTER_MAXIMUM_LENGTH")); else assertEquals(length, result.getLong("CHARACTER_MAXIMUM_LENGTH"));
            assertEquals(charset, result.getString("CHARACTER_SET_NAME"));
            assertEquals(collation, result.getString("COLLATION_NAME"));
            assertEquals(defaultValue, result.getString("COLUMN_DEFAULT"));
            assertEquals(nullable, result.getString("IS_NULLABLE"));
        }
    }

    private static int columnCount(Connection connection, String name) throws Exception {
        try (Statement statement = connection.createStatement(); ResultSet result = statement.executeQuery("SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='ai_device' AND COLUMN_NAME='" + name + "'")) {
            result.next();
            return result.getInt(1);
        }
    }
}
