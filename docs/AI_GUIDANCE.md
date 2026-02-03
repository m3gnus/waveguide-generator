# AI Guidance in MWG - Mathematical Waveguide Generator Design Platform

This document describes how the AI-assisted design features work within the MWG - Mathematical Waveguide Generator Design Platform.

## Overview

Phase 7 introduces an AI layer that learns from previous simulations and optimizations to provide design guidance, accelerate convergence, and generate human-readable insights. The AI system operates entirely locally and never replaces engineering judgment.

## Core AI Modules

### 1. Knowledge Capture (`src/ai/knowledge/`)

The knowledge module captures and structures all system outputs for learning:

- **Geometry parameters**: All horn design parameters (a0, r0, k, q, tmax, etc.)
- **Mesh parameters**: Mesh resolution, subdomain settings, etc.
- **Solver settings**: BEM configuration, frequency ranges, etc.
- **Objective scores**: Acoustic quality metrics from optimization
- **Derived metrics**: DI slope, ripple likelihood, bandwidth, phase smoothness, etc.
- **Normalized feature vectors**: For machine learning compatibility

**Storage Format:**
```json
{
  "id": "design-12345",
  "timestamp": "2026-01-29T10:30:00Z",
  "data": {
    "config": {
      "modelType": "R-OSSE",
      "parameters": {
        "a0": 15.5,
        "r0": 12.7,
        "k": 2.0,
        "q": 3.4,
        "tmax": 1.0
      }
    },
    "mesh": {
      "resolution": "medium",
      "subdomains": 3
    },
    "solver": {
      "frequencyRange": [20, 20000],
      "numFrequencies": 100
    },
    "objectives": {
      "score": 0.85,
      "ripple": 2.1,
      "diSlope": 0.45,
      "bandwidth": 8000
    },
    "metrics": {
      "phaseSmoothness": 0.78,
      "rippleLikelihood": 0.32,
      "diSlope": 0.45
    }
  },
  "metadata": {
    "hornModelType": "R-OSSE",
    "frequencyRange": [20, 20000],
    "driverAssumptions": "Standard",
    "optimizationStrategy": "Bayesian"
  }
}
```

### 2. Surrogate Modeling (`src/ai/surrogate/`)

Surrogate models reduce BEM simulation costs by approximating results:

- **Simple regression models**: Linear and polynomial for quick approximations
- **Gaussian Process**: For small datasets with uncertainty estimation
- **Neural surrogate**: Future-proofed but not implemented in this phase

**Capabilities:**
- Predict approximate on-axis response (SPL vs frequency)
- Estimate DI trend and ripple likelihood
- Provide uncertainty estimates for predictions
- Decide when full BEM is required vs skippable

### 3. Optimization Guidance (`src/ai/optimization/`)

AI-guided optimization algorithms:

- **Bayesian Optimization**: Uses Gaussian Process models to intelligently select parameter regions
- **CMA-ES Adapter**: AI-guided initialization for evolution strategy algorithms
- **Active learning loops**: Learn from previous optimization steps

**Features:**
- Parameter importance ranking based on historical data
- Adaptive bounds tightening suggestions
- Early termination recommendations
- Integration with existing optimization engine

### 4. Human-Readable Insights (`src/ai/insights/`)

Generates explanations in natural language:

- **Textual explanations**: "Mouth flare dominates DI stability above 3 kHz"
- **Sensitivity summaries**: Which parameters most affect performance
- **Trade-off explanations**: "Smoothness vs efficiency trade-offs"

**Design Principles:**
- Deterministic and traceable to data
- Non-hallucinatory (no made-up information)
- Human-readable and actionable

### 5. Preset Evolution (`src/ai/presets/`)

AI improves presets over time:

- Tracks preset performance history
- Suggests refinements based on learning from similar designs
- Allows branching of presets for different use cases
- Never silently overwrites existing presets

## AI Design Principles

### 1. Assist, Don't Replace
AI must assist engineering judgment, not replace it. All AI suggestions are presented as recommendations with confidence levels.

### 2. Traceability
All AI outputs must be traceable to data and logic:
- Insights reference specific parameter values and metrics
- Suggestions explain their reasoning based on historical patterns
- Uncertainty estimates show confidence in predictions

### 3. Deterministic Outputs
AI-generated insights must be deterministic and reproducible:
- Same input parameters always produce same insights
- Historical data analysis is consistent across runs
- No randomization in insight generation (except for randomness in training)

### 4. No Black Boxes
All AI decisions must have explanations:
- Parameter importance scores are based on historical variation patterns
- Prediction confidence comes from surrogate model uncertainty
- Optimization suggestions explain their rationale

## Integration Points

### 1. Knowledge Capture Integration
All system outputs are captured and stored in the knowledge base for learning:

```
[Geometry Generation] → [Knowledge Storage]
[BEM Simulation] → [Knowledge Storage]
[Optimization Run] → [Knowledge Storage]
```

### 2. Surrogate Modeling Integration
Surrogate models are used to guide BEM simulation decisions:

```
[Optimization Step] → [Surrogate Prediction]
[Surrogate Uncertainty] → [BEM Run Decision]
```

### 3. Optimization Guidance Integration
AI guidance is integrated into the existing optimization engine:

```
[Optimization Engine] → [AI Guidance]
[Parameter Suggestions] → [Optimization Engine]
```

### 4. Insight Generation Integration
Human-readable insights are generated for every design:

```
[Design Complete] → [AI Insight Generation]
[Insights Displayed] → [User Feedback]
```

## AI Workflow Example

1. **Design Creation**: User creates a new horn design with parameters
2. **Knowledge Capture**: All design data is stored in knowledge base
3. **BEM Simulation**: System runs acoustic simulation
4. **Knowledge Update**: Results stored in knowledge base
5. **Surrogate Training**: Historical data used to train surrogate models
6. **Optimization Guidance**: AI suggests better parameter regions for next iteration
7. **Insight Generation**: Human-readable explanations of design characteristics

## Data Flow Diagram

```
┌─────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│   User Design   │    │   System Output  │    │   AI Learning    │
│     Input       │───▶│    Capture       │───▶│   & Analysis     │
└─────────────────┘    └──────────────────┘    └──────────────────┘
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│   BEM Simulation│    │   Knowledge DB   │    │   Surrogate      │
│     Results     │◀───│   Storage        │◀───│   Models         │
└─────────────────┘    └──────────────────┘    └──────────────────┘
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│   Optimization  │    │   AI Guidance    │    │   Human Insights │
│   Engine        │◀───│   Integration    │◀───│   Generation     │
└─────────────────┘    └──────────────────┘    └──────────────────┘
```

## Technical Constraints

### No Physics Replacement
AI cannot replace physics with machine learning models. All acoustic predictions are based on BEM simulations or well-established acoustic principles.

### No Opaque Models
All AI decisions must have explanations and traceability. No black-box models without clear reasoning.

### No Internet Connectivity Required
All AI operations run entirely locally. No internet connectivity is required for AI features.

### No Silent Overwrites
AI never silently overwrites presets or configurations. All changes require explicit user approval.

## Future Enhancements

### Phase 8: Advanced AI Features
- Neural network surrogate models for complex design spaces
- Reinforcement learning for optimization policy
- Multi-objective optimization with Pareto front analysis
- Automated preset generation based on use-case classification

### Phase 9: Collaborative AI
- Cloud-based knowledge sharing for community improvements
- Distributed learning across multiple users
- AI model versioning and update management

## Usage Examples

### Example 1: AI Optimization Guidance
```
User runs optimization with current parameters.
AI analyzes historical data and suggests:
- "Parameter 'k' is highly important (importance: 0.82) - consider tightening bounds"
- "Parameter 'q' shows moderate sensitivity (importance: 0.45) - explore variations"
```

### Example 2: AI Insight Generation
```
User completes a design.
AI generates insights:
- "High directivity index slope above 0.5 dB/octave, indicating strong directional response"
- "Low likelihood of directivity ripple (0.15), suggesting stable radiation characteristics"
```

### Example 3: Surrogate Model Decision
```
User initiates new design.
AI evaluates surrogate model uncertainty:
- "Prediction uncertainty is 0.12 - running full BEM for accurate results"
```

## Configuration and Settings

### AI Feature Toggles
All AI features can be enabled/disabled through configuration:

```javascript
{
  "ai": {
    "knowledgeCapture": true,
    "surrogateModels": true,
    "optimizationGuidance": true,
    "insightGeneration": true,
    "presetEvolution": true
  }
}
```

### Confidence Thresholds
AI suggestions include confidence levels:

```javascript
{
  "confidence": 0.85,
  "explanation": "Based on historical design patterns, these parameter adjustments may improve performance",
  "suggestions": {
    "parameterName": {
      "suggestedValue": 15.2,
      "confidence": 0.85
    }
  }
}
```

## Validation and Testing

### AI Output Validation
All AI-generated outputs are validated to ensure:
- Consistency with historical data patterns
- Reasonableness of suggested parameter adjustments
- Proper uncertainty estimation in surrogate models

### Performance Testing
AI features are tested for:
- Memory usage (no excessive data storage)
- Processing time (minimal impact on user experience)
- Accuracy of predictions vs actual BEM results

## Monitoring and Debugging

### AI Logs
AI operations are logged with:
- Timestamps for tracking learning progress
- Confidence levels for each suggestion
- Data sources for traceability

### Performance Metrics
Key metrics tracked:
- Prediction accuracy of surrogate models
- Optimization convergence speed improvements
- User acceptance of AI suggestions

## API Endpoints

### Knowledge Capture
```
POST /api/ai/knowledge/capture
GET /api/ai/knowledge/history
```

### Optimization Guidance
```
POST /api/ai/optimization/suggest
GET /api/ai/optimization/progress
```

### Insight Generation
```
POST /api/ai/insights/generate
GET /api/ai/insights/history
```

### Surrogate Models
```
POST /api/ai/surrogate/train
GET /api/ai/surrogate/predict
```

## Versioning and Backward Compatibility

### Schema Versioning
Knowledge schema includes version information to ensure backward compatibility:

```json
{
  "schemaVersion": "1.0",
  "data": { /* ... */ },
  "metadata": { /* ... */ }
}
```

### Migration Strategy
When knowledge schema changes, the system automatically migrates older records to maintain learning continuity.

## Security Considerations

### Data Privacy
- All AI training data is stored locally
- No external data transmission occurs
- User configuration and design data never leaves the system

### Model Security
- AI models are trained on local historical data only
- No third-party model dependencies
- All AI logic runs in the browser or local Python backend

## Troubleshooting

### Common Issues
1. **AI suggestions not appearing**: Check that knowledge capture is enabled and sufficient historical data exists
2. **Slow optimization**: Surrogate models may need more training data to provide accurate predictions
3. **Confidence levels too low**: Historical data may be insufficient for reliable AI guidance

### Debugging Tools
- AI knowledge database inspection
- Surrogate model training status
- Optimization progress monitoring

## User Interface Integration

### Suggestions Panel
AI suggestions appear in a dedicated panel with:
- Confidence indicators
- Explanation text
- Action buttons for applying suggestions

### "Why This Design?" Button
Users can click to get AI-generated explanations of:
- Why a design performs well or poorly
- Which parameters are most important
- What trade-offs were made

### Optimization Hints
Before running optimizations, users see:
- Parameter importance rankings
- Suggested bounds tightening
- Early termination recommendations

## Performance Considerations

### Memory Usage
AI features are designed to:
- Use minimal memory for knowledge storage
- Cache only necessary historical data
- Clear temporary AI state after optimization sessions

### Processing Time
AI features are optimized to:
- Minimize impact on real-time user interactions
- Batch processing where appropriate
- Use efficient algorithms for small datasets

## Extensibility

### Adding New AI Models
New AI models can be added by:
1. Creating a new module in `src/ai/surrogate/`
2. Implementing the required interface
3. Registering the model in the surrogate manager

### Extending Knowledge Capture
New knowledge types can be added by:
1. Updating the knowledge schema in `src/ai/knowledge/schema.js`
2. Implementing storage logic in `src/ai/knowledge/storage.js`
3. Adding new fields to the knowledge capture process

## Documentation and Examples

### Example AI-Enhanced Optimization Run
```
Step 1: User starts new design with parameters [a0=15.5, r0=12.7, k=2.0]
Step 2: AI captures design knowledge and suggests parameter importance
Step 3: BEM simulation runs and results stored in knowledge base
Step 4: AI analyzes historical data to suggest next parameter region
Step 5: User receives insight: "Mouth flare dominates DI stability above 3kHz"
Step 6: AI suggests parameter adjustment for 'k' to improve directivity
```

### Example Insight Generation
```
Design Parameters:
- a0: 15.5° (Throat Angle)
- r0: 12.7mm (Throat Radius) 
- k: 2.0 (Expansion Rate)
- q: 3.4 (Shape Factor)
- tmax: 1.0 (Truncation)

AI Generated Insights:
1. "Throat angle of 15.5° is within optimal range for balanced directivity"
2. "Throat radius of 12.7mm is within typical range for efficient horn design"
3. "Expansion rate of 2.0 is within typical range for balanced horn geometry"
4. "High directivity index slope above 0.5 dB/octave, indicating strong directional response"
```

This documentation provides a complete overview of how AI features are integrated into the MWG - Mathematical Waveguide Generator Design Platform, ensuring that users understand both the capabilities and constraints of these AI-assisted design tools.