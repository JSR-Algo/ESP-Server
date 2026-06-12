// Import requests for each module
import admin from './module/admin.js'
import agent from './module/agent.js'
import device from './module/device.js'
import dict from './module/dict.js'
import model from './module/model.js'
import ota from './module/ota.js'
import timbre from "./module/timbre.js"
import user from './module/user.js'
import voiceClone from './module/voiceClone.js'
import voiceResource from './module/voiceResource.js'
import knowledgeBase from './module/knowledgeBase.js'
import correctWord from './module/correctWord.js'
import course from './module/course.js'
import lesson from './module/lesson.js'
import monitoring from './module/monitoring.js'
import nestAuth from './module/nestAuth.js'



/**
 * API address
 * Auto-read for dev use.env.developmentFile
 * Auto-read for build use.env.productionFile
 */
const DEV_API_SERVICE = process.env.VUE_APP_API_BASE_URL

/**
 * Return API based on dev environmenturl
 * @returns {string}
 */
export function getServiceUrl() {
    return DEV_API_SERVICE
}

/**
 * Base URL for the NestJS tbot-backend authoring API (course/lesson domain),
 * reached through the dev-server `/nestjs` proxy (see vue.config.js). Distinct
 * from getServiceUrl() (manager-api at /tbot). Override the prefix with
 * VUE_APP_NESTJS_BASE_URL if a different proxy path is used in prod.
 * @returns {string}
 */
export function getNestUrl() {
    return (process.env.VUE_APP_NESTJS_BASE_URL || '/nestjs') + '/v1/admin'
}

/** requestService wrapper */
export default {
    getServiceUrl,
    getNestUrl,
    user,
    admin,
    agent,
    device,
    model,
    timbre,
    ota,
    dict,
    voiceResource,
    voiceClone,
    knowledgeBase,
    correctWord,
    course,
    lesson,
    monitoring,
    nestAuth
  }
