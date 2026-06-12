import Vue from 'vue'
import VueRouter from 'vue-router'

Vue.use(VueRouter)

const routes = [
  {
    path: '/',
    name: 'welcome',
    component: function () {
      return import('../views/login.vue')
    }
  },
  {
    path: '/role-config',
    name: 'RoleConfig',
    component: function () {
      return import('../views/roleConfig.vue')
    }
  },
  {
    path: '/voice-print',
    name: 'VoicePrint',
    component: function () {
      return import('../views/VoicePrint.vue')
    }
  },
  {
    path: '/login',
    name: 'login',
    component: function () {
      return import('../views/login.vue')
    }
  },
  {
    path: '/home',
    name: 'home',
    component: function () {
      return import('../views/home.vue')
    }
  },
  {
    path: '/register',
    name: 'Register',
    component: function () {
      return import('../views/register.vue')
    }
  },
  {
    path: '/retrieve-password',
    name: 'RetrievePassword',
    component: function () {
      return import('../views/retrievePassword.vue')
    }
  },
  // Device managementPage Route
  {
    path: '/device-management',
    name: 'DeviceManagement',
    component: function () {
      return import('../views/DeviceManagement.vue')
    }
  },
  // AddUser managementRoute
  {
    path: '/user-management',
    name: 'UserManagement',
    component: function () {
      return import('../views/UserManagement.vue')
    }
  },
  {
    path: '/model-config',
    name: 'ModelConfig',
    component: function () {
      return import('../views/ModelConfig.vue')
    }
  },
  {
    path: '/params-management',
    name: 'ParamsManagement',
    component: function () {
      return import('../views/ParamsManagement.vue')
    },
    meta: {
      requiresAuth: true,
      title: 'Parameter management'
    }
  },
  {
    path: '/knowledge-base-management',
    name: 'KnowledgeBaseManagement',
    component: function () {
      return import('../views/KnowledgeBaseManagement.vue')
    },
    meta: {
      requiresAuth: true,
      title: 'Knowledge base management'
    }
  },
  {
    path: '/knowledge-file-upload',
    name: 'KnowledgeFileUpload',
    component: function () {
      return import('../views/KnowledgeFileUpload.vue')
    },
    meta: {
      requiresAuth: true,
      title: 'Document upload management'
    }
  },

  {
    path: '/server-side-management',
    name: 'ServerSideManager',
    component: function () {
      return import('../views/ServerSideManager.vue')
    },
    meta: {
      requiresAuth: true,
      title: 'Server management'
    }
  },
  {
    path: '/ota-management',
    name: 'OtaManagement',
    component: function () {
      return import('../views/OtaManagement.vue')
    },
    meta: {
      requiresAuth: true,
      title: 'OTA management'
    }
  },
  {
    path: '/voice-resource-management',
    name: 'VoiceResourceManagement',
    component: function () {
      return import('../views/VoiceResourceManagement.vue')
    },
    meta: {
      requiresAuth: true,
      title: 'Voice resource activation'
    }
  },
  {
    path: '/voice-clone-management',
    name: 'VoiceCloneManagement',
    component: function () {
      return import('../views/VoiceCloneManagement.vue')
    },
    meta: {
      requiresAuth: true,
      title: 'Voice clone management'
    }
  },
  {
    path: '/dict-management',
    name: 'DictManagement',
    component: function () {
      return import('../views/DictManagement.vue')
    }
  },
  {
    path: '/provider-management',
    name: 'ProviderManagement',
    component: function () {
      return import('../views/ProviderManagement.vue')
    }
  },
  // Add default role management route
  {
    path: '/agent-template-management',
    name: 'AgentTemplateManagement',
    component: function () {
      return import('../views/AgentTemplateManagement.vue')
    }
  },
  // Add template quick config route
  {
    path: '/template-quick-config',
    name: 'TemplateQuickConfig',
    component: function () {
      return import('../views/TemplateQuickConfig.vue')
    }
  },
  // Function configPage Route
  {
    path: '/feature-management',
    name: 'FeatureManagement',
    component: function () {
      return import('../views/FeatureManagement.vue')
    },
    meta: {
      requiresAuth: true,
      title: 'Function config'
    }
  },
  // Replacement word management
  {
    path: '/replacement-word-management',
    name: 'ReplacementWordManagement',
    component: function () {
      return import('../views/ReplacementWordManagement.vue')
    },
    meta: {
      requiresAuth: true,
      title: 'Replacement word management'
    }
  },
  // Course customization CRUD (backed by the NestJS authoring API via /nestjs proxy)
  {
    path: '/course-management',
    name: 'CourseManagement',
    component: function () {
      return import('../views/CourseManagement.vue')
    },
    meta: {
      requiresAuth: true,
      title: 'Course management'
    }
  },
  {
    path: '/course-lessons',
    name: 'CourseLessons',
    component: function () {
      return import('../views/CourseLessons.vue')
    },
    meta: {
      requiresAuth: true,
      title: 'Course lessons'
    }
  },
  {
    path: '/lesson-editor',
    name: 'LessonEditor',
    component: function () {
      return import('../views/LessonEditor.vue')
    },
    meta: {
      requiresAuth: true,
      title: 'Lesson editor'
    }
  },
  // Admin lesson-monitoring view (backed by the NestJS API via /nestjs proxy)
  {
    path: '/lesson-monitoring',
    name: 'LessonMonitoring',
    component: function () {
      return import('../views/LessonMonitoring.vue')
    },
    meta: {
      requiresAuth: true,
      title: 'Lesson monitoring'
    }
  },
]
const router = new VueRouter({
  base: process.env.VUE_APP_PUBLIC_PATH || '/',
  routes
})

// Globally handle duplicate navigation, change to refresh page
const originalPush = VueRouter.prototype.push
VueRouter.prototype.push = function push(location) {
  return originalPush.call(this, location).catch(err => {
    if (err.name === 'NavigationDuplicated') {
      // If duplicate navigation, refresh page
      window.location.reload()
    } else {
      // OtherErrorThrow Normally
      throw err
    }
  })
}

// NeedLoginRoute requiring access
const protectedRoutes = ['home', 'RoleConfig', 'DeviceManagement', 'UserManagement', 'ModelConfig', 'KnowledgeBaseManagement', 'KnowledgeFileUpload', 'CourseManagement', 'CourseLessons', 'LessonEditor', 'LessonMonitoring']

// Route Guard
router.beforeEach((to, from, next) => {
  // Check whether route needs protection
  if (protectedRoutes.includes(to.name)) {
    // fromlocalStorageGettoken
    const token = localStorage.getItem('token')
    if (!token) {
      // not yetLogin, jump toLoginpage
      next({ name: 'login', query: { redirect: to.fullPath } })
      return
    }
  }
  next()
})

export default router
