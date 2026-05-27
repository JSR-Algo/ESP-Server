package tbot.modules.knowledge.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import tbot.modules.knowledge.rag.KnowledgeBaseAdapterFactory;

/**
 * Knowledge base config class
 * Configure knowledge base relatedBean
 */
@Configuration
public class KnowledgeBaseConfig {

    /**
     * ProvideKnowledgeBaseAdapterFactoryofBeanInstance
     * @return KnowledgeBaseAdapterFactoryInstance
     */
    @Bean
    public KnowledgeBaseAdapterFactory knowledgeBaseAdapterFactory() {
        return new KnowledgeBaseAdapterFactory();
    }
}