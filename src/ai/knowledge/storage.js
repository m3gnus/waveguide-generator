/**
 * AI Knowledge Storage Module
 * 
 * Handles storage and retrieval of design knowledge for learning.
 */

import { DesignKnowledgeSchema } from './schema.js';

/**
 * Stores design knowledge in a structured format
 * 
 * @param {Object} designData - The design data to store
 * @param {Object} designData.config - Configuration parameters
 * @param {Object} designData.mesh - Mesh parameters  
 * @param {Object} designData.solver - Solver settings
 * @param {Object} designData.objectives - Objective scores
 * @param {Object} designData.metrics - Derived metrics
 * @param {Object} designData.metadata - Metadata about the design
 * @returns {Promise<void>}
 */
export async function storeDesignKnowledge(designData) {
  // Validate the design data against the schema
  const validationResult = validateDesignKnowledge(designData);
  if (!validationResult.valid) {
    throw new Error(`Invalid design knowledge data: ${validationResult.errors.join(', ')}`);
  }

  // Create a structured knowledge record
  const knowledgeRecord = {
    id: generateKnowledgeId(designData),
    timestamp: new Date().toISOString(),
    version: '1.0.0',
    data: designData
  };

  // Store in localStorage (or file system in production)
  try {
    const existingKnowledge = JSON.parse(localStorage.getItem('ai-knowledge') || '[]');
    existingKnowledge.push(knowledgeRecord);
    localStorage.setItem('ai-knowledge', JSON.stringify(existingKnowledge));
  } catch (error) {
    console.error('Failed to store design knowledge:', error);
    throw error;
  }
}

/**
 * Validates design knowledge data against the schema
 * 
 * @param {Object} designData - The design data to validate
 * @returns {Object} Validation result with valid flag and errors array
 */
function validateDesignKnowledge(designData) {
  const errors = [];
  
  // Check required fields
  if (!designData.config) errors.push('Missing config');
  if (!designData.mesh) errors.push('Missing mesh');
  if (!designData.solver) errors.push('Missing solver');
  if (!designData.objectives) errors.push('Missing objectives');
  if (!designData.metrics) errors.push('Missing metrics');
  if (!designData.metadata) errors.push('Missing metadata');
  
  // Validate config structure
  if (designData.config && !designData.config.modelType) {
    errors.push('Config missing modelType');
  }
  
  // Validate metadata structure
  if (designData.metadata && !designData.metadata.hornModelType) {
    errors.push('Metadata missing hornModelType');
  }
  
  return {
    valid: errors.length === 0,
    errors
  };
}

/**
 * Generates a unique ID for knowledge records
 * 
 * @param {Object} designData - The design data to generate ID for
 * @returns {string} Unique identifier for the knowledge record
 */
function generateKnowledgeId(designData) {
  // Simple hash-based ID generation
  const dataString = JSON.stringify({
    modelType: designData.config?.modelType || 'unknown',
    timestamp: new Date().toISOString(),
    objectives: designData.objectives
  });
  
  let hash = 0;
  for (let i = 0; i < dataString.length; i++) {
    const char = dataString.charCodeAt(i);
    hash = ((hash << 5) - hash) + char;
    hash = hash & hash; // Convert to 32-bit integer
  }
  
  return `knowledge_${Math.abs(hash).toString(16)}_${Date.now()}`;
}

/**
 * Retrieves stored design knowledge
 * 
 * @param {Object} filter - Filter criteria for retrieval
 * @returns {Promise<Array>} Array of stored knowledge records
 */
export async function retrieveDesignKnowledge(filter = {}) {
  try {
    const storedKnowledge = JSON.parse(localStorage.getItem('ai-knowledge') || '[]');
    
    // Apply filters if provided
    if (filter.modelType) {
      return storedKnowledge.filter(record => 
        record.data.config?.modelType === filter.modelType
      );
    }
    
    return storedKnowledge;
  } catch (error) {
    console.error('Failed to retrieve design knowledge:', error);
    return [];
  }
}

/**
 * Gets statistics about stored knowledge
 * 
 * @returns {Promise<Object>} Knowledge statistics
 */
export async function getKnowledgeStats() {
  try {
    const storedKnowledge = JSON.parse(localStorage.getItem('ai-knowledge') || '[]');
    
    return {
      totalRecords: storedKnowledge.length,
      modelTypes: [...new Set(storedKnowledge.map(record => record.data.config?.modelType || 'unknown'))],
      lastUpdated: storedKnowledge.length > 0 
        ? storedKnowledge[storedKnowledge.length - 1].timestamp 
        : null
    };
  } catch (error) {
    console.error('Failed to get knowledge stats:', error);
    return { totalRecords: 0, modelTypes: [], lastUpdated: null };
  }
}