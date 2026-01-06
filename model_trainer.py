"""
Machine Learning model trainer for warehouse picking time prediction
Uses scikit-learn to build predictive models for optimization
"""
import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
from sklearn.preprocessing import LabelEncoder, StandardScaler
import joblib
import os

class PickingTimePredictor:
    """
    Machine learning model to predict picking times based on item and context features
    """
    
    def __init__(self):
        self.model = None
        self.scaler = StandardScaler()
        self.label_encoders = {}
        self.feature_columns = []
        self.is_trained = False
        
    def prepare_features(self, df):
        """
        Prepare features for machine learning model
        """
        if df.empty:
            return pd.DataFrame()
        
        # Create a copy to avoid modifying original data
        features_df = df.copy()
        
        # Numerical features
        numerical_features = [
            'walking_time_seconds',
            'picking_time_seconds', 
            'confirmation_time_seconds',
            'weight_per_unit',
            'requested_qty',
            'picked_qty'
        ]
        
        # Categorical features to encode
        categorical_features = [
            'zone',
            'unit_type',
            'picker_username'
        ]
        
        # Time-based features
        if 'start_time' in features_df.columns:
            features_df['hour_of_day'] = pd.to_datetime(features_df['start_time']).dt.hour
            features_df['day_of_week'] = pd.to_datetime(features_df['start_time']).dt.dayofweek
            features_df['is_weekend'] = features_df['day_of_week'].isin([5, 6]).astype(int)
            features_df['is_peak_hour'] = features_df['hour_of_day'].isin([8, 9, 13, 14]).astype(int)
            
            numerical_features.extend(['hour_of_day', 'day_of_week', 'is_weekend', 'is_peak_hour'])
        
        # Location complexity features
        if 'level' in features_df.columns:
            features_df['level_numeric'] = pd.to_numeric(features_df['level'], errors='coerce').fillna(1)
            numerical_features.append('level_numeric')
        
        if 'corridor' in features_df.columns:
            features_df['corridor_numeric'] = pd.to_numeric(features_df['corridor'], errors='coerce').fillna(0)
            numerical_features.append('corridor_numeric')
        
        # Item complexity score
        features_df['item_complexity'] = 0
        if 'unit_type' in features_df.columns:
            complexity_map = {'EACH': 1, 'BOX': 2, 'CASE': 3, 'PALLET': 4}
            features_df['item_complexity'] = features_df['unit_type'].map(complexity_map).fillna(1)
            numerical_features.append('item_complexity')
        
        # Weight category
        if 'weight_per_unit' in features_df.columns:
            features_df['weight_category'] = pd.cut(
                features_df['weight_per_unit'].fillna(0), 
                bins=[0, 0.5, 2, 10, float('inf')], 
                labels=[1, 2, 3, 4]
            ).astype(float)
            numerical_features.append('weight_category')
        
        # Prepare final feature set
        final_features = []
        
        # Add numerical features
        for col in numerical_features:
            if col in features_df.columns:
                features_df[col] = pd.to_numeric(features_df[col], errors='coerce').fillna(0)
                final_features.append(col)
        
        # Encode categorical features
        for col in categorical_features:
            if col in features_df.columns:
                if col not in self.label_encoders:
                    self.label_encoders[col] = LabelEncoder()
                    # Fit the encoder
                    valid_values = features_df[col].dropna().astype(str)
                    if not valid_values.empty:
                        self.label_encoders[col].fit(valid_values)
                
                # Transform values
                if col in self.label_encoders:
                    features_df[col + '_encoded'] = features_df[col].astype(str).fillna('unknown')
                    
                    # Handle unseen categories
                    known_classes = set(self.label_encoders[col].classes_)
                    features_df[col + '_encoded'] = features_df[col + '_encoded'].apply(
                        lambda x: x if x in known_classes else 'unknown'
                    )
                    
                    # Add 'unknown' to encoder if not present
                    if 'unknown' not in self.label_encoders[col].classes_:
                        self.label_encoders[col].classes_ = np.append(self.label_encoders[col].classes_, 'unknown')
                    
                    features_df[col + '_encoded'] = self.label_encoders[col].transform(features_df[col + '_encoded'])
                    final_features.append(col + '_encoded')
        
        self.feature_columns = final_features
        return features_df[final_features]
    
    def train_model(self, df, target_column='total_time_seconds'):
        """
        Train the picking time prediction model
        """
        if df.empty:
            raise ValueError("Cannot train model with empty dataset")
        
        # Prepare features
        X = self.prepare_features(df)
        
        if X.empty:
            raise ValueError("No valid features could be prepared from the data")
        
        # Prepare target variable
        y = pd.to_numeric(df[target_column], errors='coerce')
        
        # Remove rows with missing target values
        valid_rows = ~(y.isna() | X.isna().any(axis=1))
        X = X[valid_rows]
        y = y[valid_rows]
        
        if len(X) < 10:
            raise ValueError("Insufficient data for training (minimum 10 samples required)")
        
        # Scale features
        X_scaled = self.scaler.fit_transform(X)
        
        # Split data
        X_train, X_test, y_train, y_test = train_test_split(
            X_scaled, y, test_size=0.2, random_state=42
        )
        
        # Try multiple models and select the best one
        models = {
            'RandomForest': RandomForestRegressor(n_estimators=100, random_state=42, max_depth=10),
            'GradientBoosting': GradientBoostingRegressor(n_estimators=100, random_state=42, max_depth=6),
            'LinearRegression': LinearRegression()
        }
        
        best_model = None
        best_score = float('-inf')
        model_scores = {}
        
        for name, model in models.items():
            try:
                # Cross-validation
                cv_scores = cross_val_score(model, X_train, y_train, cv=3, scoring='r2')
                avg_score = cv_scores.mean()
                model_scores[name] = avg_score
                
                if avg_score > best_score:
                    best_score = avg_score
                    best_model = model
                    
            except Exception as e:
                print(f"Error training {name}: {e}")
                continue
        
        if best_model is None:
            raise ValueError("No model could be successfully trained")
        
        # Train the best model on full training set
        best_model.fit(X_train, y_train)
        self.model = best_model
        
        # Calculate performance metrics
        y_pred = best_model.predict(X_test)
        metrics = {
            'r2_score': r2_score(y_test, y_pred),
            'mse': mean_squared_error(y_test, y_pred),
            'mae': mean_absolute_error(y_test, y_pred),
            'rmse': np.sqrt(mean_squared_error(y_test, y_pred)),
            'model_type': type(best_model).__name__,
            'training_samples': len(X_train),
            'test_samples': len(X_test),
            'cross_val_scores': model_scores
        }
        
        self.is_trained = True
        
        return metrics
    
    def predict_time(self, df):
        """
        Predict picking times for new data
        """
        if not self.is_trained or self.model is None:
            raise ValueError("Model must be trained before making predictions")
        
        if df.empty:
            return np.array([])
        
        # Prepare features
        X = self.prepare_features(df)
        
        if X.empty:
            return np.array([])
        
        # Handle missing feature columns
        for col in self.feature_columns:
            if col not in X.columns:
                X[col] = 0
        
        # Reorder columns to match training
        X = X[self.feature_columns]
        
        # Scale features
        X_scaled = self.scaler.transform(X)
        
        # Make predictions
        predictions = self.model.predict(X_scaled)
        
        return predictions
    
    def get_feature_importance(self):
        """
        Get feature importance from the trained model
        """
        if not self.is_trained or self.model is None:
            return {}
        
        if hasattr(self.model, 'feature_importances_'):
            importance_dict = dict(zip(self.feature_columns, self.model.feature_importances_))
            # Sort by importance
            return dict(sorted(importance_dict.items(), key=lambda x: x[1], reverse=True))
        else:
            return {}
    
    def save_model(self, filepath):
        """
        Save the trained model to disk
        """
        if not self.is_trained:
            raise ValueError("Cannot save untrained model")
        
        model_data = {
            'model': self.model,
            'scaler': self.scaler,
            'label_encoders': self.label_encoders,
            'feature_columns': self.feature_columns,
            'is_trained': self.is_trained
        }
        
        joblib.dump(model_data, filepath)
    
    def load_model(self, filepath):
        """
        Load a trained model from disk
        """
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Model file not found: {filepath}")
        
        model_data = joblib.load(filepath)
        
        self.model = model_data['model']
        self.scaler = model_data['scaler']
        self.label_encoders = model_data['label_encoders']
        self.feature_columns = model_data['feature_columns']
        self.is_trained = model_data['is_trained']

def train_and_evaluate_model(df):
    """
    Convenience function to train and evaluate a picking time prediction model
    """
    predictor = PickingTimePredictor()
    
    try:
        metrics = predictor.train_model(df)
        feature_importance = predictor.get_feature_importance()
        
        return {
            'success': True,
            'metrics': metrics,
            'feature_importance': feature_importance
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }